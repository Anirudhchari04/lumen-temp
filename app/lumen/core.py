"""Lumen Core — The persistent, person-centric agent state.

A Lumen maintains identity, learning state, and session history
across multiple TAs and sessions. Backed by Cosmos DB when available,
falls back to in-memory store for demo/local dev.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.cosmos import get_lumen as _cosmos_get, upsert_lumen as _cosmos_upsert, is_cosmos_ready

logger = logging.getLogger(__name__)


def _resolve_store_path() -> Path:
    """Resolve where to persist the in-memory Lumen store.

    Priority:
      1. settings.lumen_store_path (env: LUMEN_STORE_PATH)
      2. /home/data/lumens.json  (Azure App Service persistent volume)
      3. ./data/lumens.json      (local dev)
    """
    if settings.lumen_store_path:
        return Path(settings.lumen_store_path)
    if os.path.isdir("/home"):
        return Path("/home/data/lumens.json")
    return Path("data/lumens.json")


_STORE_PATH = _resolve_store_path()


def _load_store() -> dict[str, dict]:
    try:
        if _STORE_PATH.exists():
            with _STORE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                logger.info(f"Loaded {len(data)} Lumens from {_STORE_PATH}")
                return data
    except Exception as e:
        logger.warning(f"Failed to load Lumen store at {_STORE_PATH}: {e}")
    return {}


def _flush_store() -> None:
    """Persist _lumens to disk. Best-effort, never raises."""
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(_STORE_PATH.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_lumens, f, ensure_ascii=False, indent=2)
        tmp.replace(_STORE_PATH)
    except Exception as e:
        logger.warning(f"Failed to persist Lumen store at {_STORE_PATH}: {e}")


# In-memory fallback (used when Cosmos is not configured), hydrated from disk.
_lumens: dict[str, dict] = _load_store()


def _now() -> str:
    return datetime.now(UTC).isoformat()


import re as _re

# Reserved slugs that must never be assigned as a username (avoid route/path clashes).
_RESERVED_USERNAMES = {
    "api", "auth", "admin", "lumen", "chat", "agents", "static", "public",
    "health", "u", "v2", "me", "peers", "share", "login", "logout", "settings",
    "www", "app", "assets", "favicon",
}

USERNAME_RE = _re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")


def _slugify(text: str) -> str:
    """Lowercase, ASCII, hyphen-separated slug suitable for a URL path segment."""
    text = (text or "").strip().lower()
    text = _re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:30]


def generate_username(name: str, email: str) -> str:
    """Build a base username slug from a name (preferred) or email local-part."""
    base = _slugify(name) or _slugify((email or "").split("@")[0]) or "lumen"
    # A bare reserved word or too-short slug gets a stable suffix.
    if base in _RESERVED_USERNAMES or len(base) < 3:
        base = f"{base}-user"
    return base


def is_valid_username(username: str) -> bool:
    """Validate a user-chosen username: 3–30 chars, [a-z0-9-], not reserved."""
    if not username or username in _RESERVED_USERNAMES:
        return False
    return bool(USERNAME_RE.match(username))


def _default_lumen(user_id: str, name: str, email: str, **kwargs) -> dict:
    """Build a default Lumen document."""
    return {
        "id": user_id,
        "lumen_id": f"lumen://{kwargs.get('tenant_id', 'default')}/{user_id}",
        "username": kwargs.get("username") or generate_username(name, email),
        "name": name,
        "email": email,
        "org": email.split("@")[1] if "@" in email else "",
        "bio": kwargs.get("bio", ""),
        "expertise": kwargs.get("expertise", ""),
        "interests": kwargs.get("interests", ""),
        "preferences": {
            "language": "English",
            "pace": "moderate",
            "explanation": "detailed",
        },
        "curriculum_progress": {},
        "tc_inventory": {
            "mastered": [],
            "in_progress": [],
        },
        "session_history": [],
        "artifacts": [],
        "skills": [],
        "social": {"discoverable": True, "share_progress": True},
        "created_at": _now(),
        "updated_at": _now(),
    }


async def get_lumen(user_id: str) -> dict | None:
    """Get a Lumen by user ID (Cosmos first, then memory)."""
    if is_cosmos_ready():
        try:
            doc = await _cosmos_get(user_id)
            if doc:
                return doc
        except Exception as e:
            logger.warning(f"core.get_lumen fallback for {user_id}: {e}")
    return _lumens.get(user_id)


async def save_lumen(lumen: dict) -> dict:
    """Persist a Lumen (Cosmos or memory+disk)."""
    lumen["updated_at"] = _now()
    if is_cosmos_ready():
        return await _cosmos_upsert(lumen)
    _lumens[lumen["id"]] = lumen
    _flush_store()
    return lumen


async def get_or_create_lumen(user_id: str, name: str, email: str, **kwargs) -> dict:
    """Get existing Lumen or create a new one."""
    lumen = await get_lumen(user_id)
    if not lumen:
        lumen = _default_lumen(user_id, name, email, **kwargs)
        # Guarantee the auto-generated username is unique across the network.
        lumen["username"] = await _allocate_unique_username(lumen["username"], user_id)
        lumen = await save_lumen(lumen)
    return lumen


async def get_lumen_profile(user_id: str) -> dict | None:
    """Get the public profile portion of a Lumen (what TAs read)."""
    lumen = await get_lumen(user_id)
    if not lumen:
        return None
    return {
        "id": lumen["id"],
        "lumen_id": lumen["lumen_id"],
        "name": lumen["name"],
        "bio": lumen.get("bio", ""),
        "expertise": lumen.get("expertise", ""),
        "interests": lumen.get("interests", ""),
        "preferences": lumen.get("preferences", {}),
    }


async def get_lumen_state(user_id: str, ta_id: str | None = None) -> dict | None:
    """Get learning state. If ta_id provided, returns state for that TA
    with cross-TA context."""
    lumen = await get_lumen(user_id)
    if not lumen:
        return None

    progress = lumen.get("curriculum_progress", {})

    if ta_id:
        ta_state = progress.get(ta_id, {})
        other_ta_summaries = []
        for tid, state in progress.items():
            if tid != ta_id:
                other_ta_summaries.append({
                    "ta_id": tid,
                    "ta_name": state.get("ta_name", tid),
                    "topics_covered": state.get("topics_covered", []),
                    "topics_mastered": state.get("topics_mastered", []),
                    "current_level": state.get("current_level", 1),
                    "current_module": state.get("current_module", ""),
                    "level_label": state.get("level_label", "beginner"),
                    "last_summary": state.get("last_summary", ""),
                })
        return {
            "current_ta_state": ta_state,
            "cross_ta_context": other_ta_summaries,
            "tc_inventory": lumen.get("tc_inventory", {"mastered": [], "in_progress": []}),
            "recent_sessions": lumen.get("session_history", [])[-5:],
        }

    return {
        "curriculum_progress": progress,
        "tc_inventory": lumen.get("tc_inventory", {"mastered": [], "in_progress": []}),
        "session_history": lumen.get("session_history", [])[-10:],
        "artifacts": lumen.get("artifacts", [])[-10:],
    }


async def update_progress(user_id: str, ta_id: str, ta_name: str,
                          progress_data: dict) -> dict:
    """Update curriculum progress and TC inventory from LLM analysis."""
    lumen = await get_lumen(user_id)
    if not lumen:
        return {"error": "Lumen not found"}

    now = _now()
    progress = lumen.setdefault("curriculum_progress", {})

    # Update per-TA progress
    if ta_id not in progress:
        progress[ta_id] = {"ta_name": ta_name, "topics_covered": [], "topics_mastered": [],
                           "session_count": 0, "current_level": 1}

    ta = progress[ta_id]
    ta["ta_name"] = ta_name
    ta["session_count"] = ta.get("session_count", 0) + 1
    ta["last_session"] = now

    if progress_data.get("topics_covered"):
        existing = set(ta.get("topics_covered", []))
        existing.update(progress_data["topics_covered"])
        ta["topics_covered"] = list(existing)

    if progress_data.get("topics_mastered"):
        existing = set(ta.get("topics_mastered", []))
        existing.update(progress_data["topics_mastered"])
        ta["topics_mastered"] = list(existing)

    if progress_data.get("current_level"):
        ta["current_level"] = progress_data["current_level"]
    if progress_data.get("current_module"):
        ta["current_module"] = progress_data["current_module"]
    if progress_data.get("level_label"):
        ta["level_label"] = progress_data["level_label"]
    if progress_data.get("summary"):
        ta["last_summary"] = progress_data["summary"]

    # Update TC inventory
    tc_inv = lumen.setdefault("tc_inventory", {"mastered": [], "in_progress": []})
    tc_updates = progress_data.get("tc_updates", {})
    mastered_ids = {t["tc_id"] for t in tc_inv["mastered"]}

    for tc_id, update in tc_updates.items():
        if update.get("status") == "mastered" and tc_id not in mastered_ids:
            tc_inv["mastered"].append({
                "tc_id": tc_id,
                "evidence": update.get("evidence", ""),
                "crossed_at": now,
                "source_ta": ta_id,
            })
            tc_inv["in_progress"] = [t for t in tc_inv["in_progress"] if t.get("tc_id") != tc_id]
        elif update.get("status") == "in_progress":
            existing_ip = next((t for t in tc_inv["in_progress"] if t["tc_id"] == tc_id), None)
            if existing_ip:
                existing_ip["progress_pct"] = update.get("progress_pct", 50)
                existing_ip["obstacles"] = update.get("obstacles", "")
                existing_ip["last_updated"] = now
            elif tc_id not in mastered_ids:
                tc_inv["in_progress"].append({
                    "tc_id": tc_id,
                    "progress_pct": update.get("progress_pct", 30),
                    "obstacles": update.get("obstacles", ""),
                    "last_updated": now,
                })

    # Add to session history
    lumen.setdefault("session_history", []).append({
        "ta_id": ta_id,
        "ta_name": ta_name,
        "timestamp": now,
        "summary": progress_data.get("summary", ""),
        "topics_covered": progress_data.get("topics_covered", []),
    })

    await save_lumen(lumen)
    return {"ok": True, "session_count": ta["session_count"]}


async def get_all_lumens() -> list[dict]:
    """Get all Lumens (for discovery/listing)."""
    if is_cosmos_ready():
        from app.db.cosmos import query_all_lumens
        all_l = await query_all_lumens()
        return [
            {"id": l["id"], "name": l.get("name", ""), "email": l.get("email", ""), "lumen_id": l.get("lumen_id", "")}
            for l in all_l
        ]
    return [
        {"id": l["id"], "name": l["name"], "email": l.get("email", ""), "lumen_id": l["lumen_id"]}
        for l in _lumens.values()
    ]


async def _all_lumens_full() -> list[dict]:
    """Return full Lumen documents (Cosmos or in-memory). Internal use only."""
    if is_cosmos_ready():
        from app.db.cosmos import query_all_lumens
        return await query_all_lumens()
    return list(_lumens.values())


async def username_exists(username: str, exclude_id: str | None = None) -> bool:
    """True if any other Lumen already owns this username (case-insensitive)."""
    username = (username or "").lower()
    for l in await _all_lumens_full():
        if exclude_id and l.get("id") == exclude_id:
            continue
        if (l.get("username") or "").lower() == username:
            return True
    return False


async def get_lumen_by_username(username: str) -> dict | None:
    """Resolve a Lumen by its public username slug (case-insensitive)."""
    username = (username or "").lower()
    if not username:
        return None
    for l in await _all_lumens_full():
        if (l.get("username") or "").lower() == username:
            return l
    return None


async def _allocate_unique_username(base: str, owner_id: str) -> str:
    """Return `base`, or `base-2`, `base-3`, … until one is free for owner_id."""
    candidate = base
    n = 1
    while await username_exists(candidate, exclude_id=owner_id):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


async def ensure_username(lumen: dict) -> dict:
    """Backfill a unique username on an existing Lumen that predates the field.

    Persists the Lumen if a username was added. Returns the (possibly updated) doc.
    """
    if lumen.get("username"):
        return lumen
    base = generate_username(lumen.get("name", ""), lumen.get("email", ""))
    lumen["username"] = await _allocate_unique_username(base, lumen["id"])
    return await save_lumen(lumen)


async def set_username(user_id: str, username: str) -> dict:
    """Set a user-chosen username after validation + uniqueness checks.

    Raises ValueError on invalid format, reserved word, or collision.
    """
    username = (username or "").strip().lower()
    if not is_valid_username(username):
        raise ValueError(
            "Username must be 3–30 chars, lowercase letters/numbers/hyphens, "
            "and not a reserved word."
        )
    lumen = await get_lumen(user_id)
    if not lumen:
        raise ValueError("Lumen not found")
    if await username_exists(username, exclude_id=user_id):
        raise ValueError("That username is already taken")
    lumen["username"] = username
    return await save_lumen(lumen)


def build_share_url(username: str) -> str:
    """Build the public, shareable Lumen link for a username.

    Subdomain form (preferred) when LUMEN_BASE_DOMAIN is configured, e.g.
    `https://manohar.lumen.org` — requires wildcard DNS + host routing.
    Otherwise falls back to the path form `https://<app_base_url>/u/manohar`.
    """
    base_domain = (getattr(settings, "lumen_base_domain", "") or "").strip().lower()
    if base_domain:
        return f"https://{username}.{base_domain}"
    base = (settings.app_base_url or "").rstrip("/")
    return f"{base}/u/{username}"


def extract_username_from_host(host: str) -> str | None:
    """Extract the Lumen username from a request Host header.

    Returns the subdomain label for `{username}.{LUMEN_BASE_DOMAIN}` hosts,
    ignoring reserved labels (www, app, api). Returns None when the host is not
    a Lumen subdomain (e.g. apex domain, localhost, or base domain not set).
    """
    base_domain = (getattr(settings, "lumen_base_domain", "") or "").strip().lower()
    if not base_domain or not host:
        return None
    host = host.split(":")[0].strip().lower()  # drop any :port
    suffix = "." + base_domain
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not label or "." in label or label in _RESERVED_USERNAMES:
        return None
    return label




# ── Sync wrappers for backward compat ────────────────────────

def get_or_create_lumen_sync(user_id: str, name: str, email: str, **kwargs) -> dict:
    """Sync fallback — only uses in-memory store."""
    lumen = _lumens.get(user_id)
    if not lumen:
        lumen = _default_lumen(user_id, name, email, **kwargs)
        _lumens[user_id] = lumen
        _flush_store()
    return lumen
