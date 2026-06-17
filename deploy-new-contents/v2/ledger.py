"""Task Ledger + Progress Ledger persistence for Lumen v2.

Magentic-One maintains two ledgers internally: a Task Ledger (facts + plan) and a
Progress Ledger (per-turn progress / who-speaks-next). We mirror both into a NEW
Cosmos container, `lumen_v2_sessions`, so a v2 run is fully auditable.

Hard rule: this module touches ONLY the v2 container. It uses its own Cosmos
client (the same endpoint + Entra credential as v1) and never imports or writes
through app.db.cosmos, so there is zero chance of writing a v1 container. If
Cosmos is unavailable it falls back to an in-memory store so local runs work.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from v2 import config

logger = logging.getLogger("lumen.v2.ledger")
UTC = timezone.utc

_client = None
_container = None
_ready = False
_init_attempted = False
_mem: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def is_ready() -> bool:
    return _ready


async def init_ledger() -> bool:
    """Create/connect the v2 sessions container. Idempotent, best-effort."""
    global _client, _container, _ready
    if not config.COSMOS_ENDPOINT:
        logger.warning("v2 ledger: COSMOS_ENDPOINT unset — using in-memory fallback")
        return False
    try:
        from azure.cosmos.aio import CosmosClient
        from azure.cosmos import PartitionKey
        from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

        cred = (
            ManagedIdentityCredential(client_id=config.AZURE_MI_CLIENT_ID)
            if config.AZURE_MI_CLIENT_ID
            else DefaultAzureCredential()
        )
        _client = CosmosClient(config.COSMOS_ENDPOINT, credential=cred)
        db = _client.get_database_client(config.COSMOS_DATABASE)
        _container = await db.create_container_if_not_exists(
            id=config.V2_SESSIONS_CONTAINER,
            partition_key=PartitionKey(path="/user_id"),
        )
        _ready = True
        logger.info("v2 ledger ready: container=%s", config.V2_SESSIONS_CONTAINER)
        return True
    except Exception as e:
        logger.warning("v2 ledger init failed (in-memory fallback): %s", e)
        _ready = False
        return False


async def ensure_ready() -> None:
    """Lazy one-shot init — v1's lifespan doesn't know about v2."""
    global _init_attempted
    if not _init_attempted:
        _init_attempted = True
        await init_ledger()


async def close_ledger() -> None:
    global _client, _ready
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
        _client = None
        _ready = False


async def _save(doc: dict) -> None:
    if _ready and _container is not None:
        try:
            await _container.upsert_item(doc)
            return
        except Exception as e:
            logger.warning("v2 ledger upsert failed, keeping in memory: %s", e)
    _mem[doc["id"]] = doc


# ── Lifecycle ───────────────────────────────────────────────────────────────

async def start_session(user_id: str, task: str, agents: list[str],
                        thread_id: str | None = None) -> dict:
    """Write initial Task Ledger when a v2 task begins."""
    sid = str(uuid.uuid4())
    doc = {
        # Cosmos requires the item key to be named "id"; session_id mirrors it so
        # consumers can query by either name.
        "id": sid,
        "session_id": sid,
        "user_id": user_id or "anon",
        "type": "lumen_v2_session",
        "status": "running",
        "task": task,
        "thread_id": thread_id,
        "task_ledger": {
            "task": task,
            "participants": agents,
            "facts": "",
            "plan": [],
        },
        "progress_log": [],
        "reply": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    await _save(doc)
    return doc


async def update_task_ledger(session: dict, facts: str | None = None,
                             plan: list | None = None) -> None:
    """Record the orchestrator's gathered facts / plan (Task Ledger)."""
    tl = session.setdefault("task_ledger", {})
    if facts is not None:
        tl["facts"] = facts
    if plan is not None:
        tl["plan"] = plan
    session["updated_at"] = _now()
    await _save(session)


async def append_progress(session: dict, entry: dict[str, Any]) -> None:
    """Append one Progress Ledger entry after an agent turn."""
    session.setdefault("progress_log", []).append({**entry, "ts": _now()})
    session["updated_at"] = _now()
    await _save(session)


async def complete_session(session: dict, reply: str = "") -> None:
    session["status"] = "completed"
    session["reply"] = reply
    session["updated_at"] = _now()
    await _save(session)


async def fail_session(session: dict, error: str = "") -> None:
    session["status"] = "failed"
    session["error"] = error
    session["updated_at"] = _now()
    await _save(session)


async def get_session(session_id: str, user_id: str) -> dict | None:
    if _ready and _container is not None:
        try:
            return await _container.read_item(item=session_id, partition_key=user_id or "anon")
        except Exception:
            return None
    return _mem.get(session_id)
