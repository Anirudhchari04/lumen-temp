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


def _default_lumen(user_id: str, name: str, email: str, **kwargs) -> dict:
    """Build a default Lumen document."""
    return {
        "id": user_id,
        "lumen_id": f"lumen://{kwargs.get('tenant_id', 'default')}/{user_id}",
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


# ── Sync wrappers for backward compat ────────────────────────

def get_or_create_lumen_sync(user_id: str, name: str, email: str, **kwargs) -> dict:
    """Sync fallback — only uses in-memory store."""
    lumen = _lumens.get(user_id)
    if not lumen:
        lumen = _default_lumen(user_id, name, email, **kwargs)
        _lumens[user_id] = lumen
        _flush_store()
    return lumen
