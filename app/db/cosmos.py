"""Cosmos DB client — async, Entra ID auth via user-assigned managed identity."""

from __future__ import annotations

import logging
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.cosmos import PartitionKey, exceptions
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

from app.config import settings

logger = logging.getLogger(__name__)

_client: CosmosClient | None = None
_db = None
_containers: dict[str, Any] = {}

# In-memory fallback stores
_mem_threads: dict[str, dict] = {}

CONTAINERS = {
    "lumens": "/id",
    "chat_threads": "/user_id",
    "graph_tokens": "/id",
    # Peer-to-peer chat messages — partitioned by channel_id so an inbox query
    # for a single conversation hits one partition. channel_id is the sorted
    # pair of user IDs joined with ":" (see peer_channel_id() helper).
    "peer_messages": "/channel_id",
    # External agent registry — keyed by endpoint URL (canonical A2A identity).
    "agents": "/id",
}


def _get_credential():
    if settings.azure_managed_identity_client_id:
        return ManagedIdentityCredential(client_id=settings.azure_managed_identity_client_id)
    return DefaultAzureCredential()


async def init_cosmos():
    """Initialize Cosmos client and ensure containers exist."""
    global _client, _db, _containers

    if not settings.cosmos_endpoint:
        logger.warning("COSMOS_ENDPOINT not set — using in-memory fallback")
        return False

    try:
        _client = CosmosClient(settings.cosmos_endpoint, credential=_get_credential())
        _db = _client.get_database_client(settings.cosmos_database)

        for name, pk in CONTAINERS.items():
            try:
                _containers[name] = _db.get_container_client(name)
                await _containers[name].read()
                logger.info(f"Cosmos container ready: {name}")
            except exceptions.CosmosResourceNotFoundError:
                logger.info(f"Creating Cosmos container: {name}")
                _containers[name] = await _db.create_container_if_not_exists(
                    id=name, partition_key=PartitionKey(path=pk)
                )

        logger.info("Cosmos DB initialized")
        return True
    except Exception as e:
        logger.warning(f"Cosmos DB init failed (using in-memory fallback): {e}")
        _client = None
        _db = None
        _containers.clear()
        return False


async def close_cosmos():
    """Close the Cosmos client."""
    global _client
    if _client:
        await _client.close()
        _client = None


def is_cosmos_ready() -> bool:
    return bool(_containers)


# ── Lumen CRUD ───────────────────────────────────────────────

async def get_lumen(user_id: str) -> dict | None:
    if not is_cosmos_ready() or "lumens" not in _containers:
        return None
    try:
        item = await _containers["lumens"].read_item(item=user_id, partition_key=user_id)
        return item
    except exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"Cosmos get_lumen failed for {user_id}: {e}")
        return None


async def upsert_lumen(lumen: dict) -> dict:
    if not is_cosmos_ready():
        return lumen
    result = await _containers["lumens"].upsert_item(lumen)
    return result


async def query_all_lumens() -> list[dict]:
    """Query all lumens across partitions (for peer discovery)."""
    if not is_cosmos_ready():
        return []
    try:
        items = []
        async for item in _containers["lumens"].query_items("SELECT * FROM c"):
            items.append(item)
        return items
    except Exception as e:
        logger.warning(f"query_all_lumens failed: {e}")
        return []


async def delete_lumen(user_id: str) -> bool:
    """Delete a user's Lumen profile + all their chat threads.

    Returns True if anything was deleted, False if user not found.
    """
    deleted = False
    if not is_cosmos_ready():
        return False
    # 1. Delete the lumen profile
    try:
        await _containers["lumens"].delete_item(item=user_id, partition_key=user_id)
        deleted = True
        logger.info(f"Deleted Lumen profile: {user_id}")
    except exceptions.CosmosResourceNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"delete_lumen profile error: {e}")

    # 2. Delete all chat threads for this user
    try:
        async for thread in _containers["chat_threads"].query_items(
            query="SELECT * FROM c WHERE c.user_id = @u",
            parameters=[{"name": "@u", "value": user_id}],
        ):
            try:
                await _containers["chat_threads"].delete_item(
                    item=thread["id"], partition_key=user_id
                )
                deleted = True
            except Exception as e:
                logger.warning(f"delete thread {thread.get('id')} failed: {e}")
    except Exception as e:
        logger.warning(f"delete chat_threads query failed: {e}")

    # 3. Best-effort: drop any cached graph tokens
    try:
        await _containers["graph_tokens"].delete_item(item=user_id, partition_key=user_id)
    except exceptions.CosmosResourceNotFoundError:
        pass
    except Exception:
        pass

    return deleted


# ── Chat Thread CRUD ─────────────────────────────────────────

async def get_thread(user_id: str, thread_id: str) -> dict | None:
    """Get a thread by ID."""
    if is_cosmos_ready():
        try:
            item = await _containers["chat_threads"].read_item(item=thread_id, partition_key=user_id)
            return item
        except exceptions.CosmosResourceNotFoundError:
            return None
    return _mem_threads.get(thread_id)


async def get_or_create_fixed_thread(user_id: str, channel: str, title: str = "") -> dict:
    """Get or create a fixed thread for a channel (lumen, math-ta, cs-ta).
    One persistent thread per channel per user."""
    from datetime import datetime, timezone as _tz
    UTC = _tz.utc

    thread_id = f"{user_id}:{channel}"
    existing = await get_thread(user_id, thread_id)
    if existing:
        return existing

    thread = {
        "id": thread_id,
        "user_id": user_id,
        "channel": channel,
        "title": title or channel,
        "messages": [],
        "message_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if is_cosmos_ready():
        await _containers["chat_threads"].upsert_item(thread)
    else:
        _mem_threads[thread_id] = thread
    return thread


async def create_thread(user_id: str, title: str = "New Chat", channel: str = "lumen") -> dict:
    """Create a new chat thread."""
    import uuid
    from datetime import datetime, timezone as _tz
    UTC = _tz.utc

    thread = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "channel": channel,
        "title": title,
        "messages": [],
        "message_count": 0,
        "last_ta": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if is_cosmos_ready():
        await _containers["chat_threads"].upsert_item(thread)
    else:
        _mem_threads[thread["id"]] = thread
    return thread


async def update_thread_title(user_id: str, thread_id: str, title: str) -> dict | None:
    """Update a thread's title."""
    thread = await get_thread(user_id, thread_id)
    if not thread:
        return None
    thread["title"] = title
    if is_cosmos_ready():
        await _containers["chat_threads"].upsert_item(thread)
    else:
        _mem_threads[thread["id"]] = thread
    return thread


async def append_message(user_id: str, thread_id: str, role: str, content: str,
                         ta_id: str | None = None,
                         progress_update: dict | None = None) -> dict:
    """Append a message to a chat thread."""
    from datetime import datetime, timezone as _tz
    UTC = _tz.utc

    thread = await get_thread(user_id, thread_id)
    if not thread:
        thread = await create_thread(user_id)

    msg = {
        "role": role,
        "content": content,
        "ts": datetime.now(UTC).isoformat(),
    }
    if ta_id:
        msg["ta_id"] = ta_id
    if progress_update:
        msg["progress_update"] = progress_update

    thread["messages"].append(msg)
    thread["message_count"] = len(thread["messages"])
    thread["updated_at"] = datetime.now(UTC).isoformat()
    if ta_id:
        thread["last_ta"] = ta_id

    # Auto-title from first user message
    if thread["title"] == "New Chat" and role == "user":
        thread["title"] = content[:50] + ("..." if len(content) > 50 else "")

    if is_cosmos_ready():
        await _containers["chat_threads"].upsert_item(thread)
    else:
        _mem_threads[thread["id"]] = thread
    return thread


async def get_all_threads(user_id: str) -> list[dict]:
    """Get all chat threads for a user."""
    if is_cosmos_ready():
        query = "SELECT c.id, c.user_id, c.channel, c.title, c.message_count, c.last_ta, c.created_at, c.updated_at FROM c WHERE c.user_id = @uid ORDER BY c.updated_at DESC"
        params = [{"name": "@uid", "value": user_id}]
        items = []
        async for item in _containers["chat_threads"].query_items(
            query, parameters=params, partition_key=user_id
        ):
            items.append(item)
        return items
    threads = [t for t in _mem_threads.values() if t["user_id"] == user_id]
    return sorted(threads, key=lambda t: t.get("updated_at", ""), reverse=True)


# ── Peer Messages CRUD ───────────────────────────────────────

def peer_channel_id(user_a: str, user_b: str) -> str:
    """Stable channel id for a pair of users. Sorted so A→B and B→A collide."""
    a, b = sorted([user_a or "", user_b or ""])
    return f"{a}:{b}"


async def add_peer_message(msg: dict) -> dict:
    """Persist a peer message. Caller must have populated from_id and to_id."""
    msg = dict(msg)
    msg["channel_id"] = peer_channel_id(msg.get("from_id", ""), msg.get("to_id", ""))
    if not is_cosmos_ready():
        return msg
    try:
        return await _containers["peer_messages"].upsert_item(msg)
    except Exception as e:
        logger.warning(f"add_peer_message failed: {e}")
        return msg


async def get_peer_messages_for_user(user_id: str) -> list[dict]:
    """All peer messages where user is sender or recipient. Cross-partition."""
    if not is_cosmos_ready():
        return []
    try:
        query = (
            "SELECT * FROM c WHERE c.from_id = @uid OR c.to_id = @uid "
            "ORDER BY c.created_at ASC"
        )
        params = [{"name": "@uid", "value": user_id}]
        items = []
        async for item in _containers["peer_messages"].query_items(
            query, parameters=params
        ):
            items.append(item)
        return items
    except Exception as e:
        logger.warning(f"get_peer_messages_for_user failed: {e}")
        return []


async def get_peer_conversation(user_a: str, user_b: str) -> list[dict]:
    """All messages between two users — single-partition read."""
    if not is_cosmos_ready():
        return []
    try:
        cid = peer_channel_id(user_a, user_b)
        query = "SELECT * FROM c WHERE c.channel_id = @cid ORDER BY c.created_at ASC"
        items = []
        async for item in _containers["peer_messages"].query_items(
            query, parameters=[{"name": "@cid", "value": cid}], partition_key=cid
        ):
            items.append(item)
        return items
    except Exception as e:
        logger.warning(f"get_peer_conversation failed: {e}")
        return []


async def mark_peer_messages_read(user_id: str, ids: list[str]) -> int:
    """Mark a set of inbox messages as read. Returns count updated."""
    if not is_cosmos_ready() or not ids:
        return 0
    updated = 0
    for mid in ids:
        # We don't know the channel_id without a read first, but partition is required.
        # Cheapest approach: cross-partition lookup by id, then upsert.
        try:
            query = "SELECT * FROM c WHERE c.id = @id"
            async for item in _containers["peer_messages"].query_items(
                query, parameters=[{"name": "@id", "value": mid}]
            ):
                if item.get("to_id") == user_id and not item.get("read"):
                    item["read"] = True
                    await _containers["peer_messages"].upsert_item(item)
                    updated += 1
        except Exception as e:
            logger.warning(f"mark_peer_messages_read({mid}) failed: {e}")
    return updated


async def get_threads_by_channel(user_id: str, channel: str) -> list[dict]:
    """Get threads for a specific channel."""
    if is_cosmos_ready():
        query = "SELECT c.id, c.user_id, c.channel, c.title, c.message_count, c.created_at, c.updated_at FROM c WHERE c.user_id = @uid AND c.channel = @ch ORDER BY c.updated_at DESC"
        params = [{"name": "@uid", "value": user_id}, {"name": "@ch", "value": channel}]
        items = []
        async for item in _containers["chat_threads"].query_items(
            query, parameters=params, partition_key=user_id
        ):
            items.append(item)
        return items
    threads = [t for t in _mem_threads.values() if t["user_id"] == user_id and t.get("channel") == channel]
    return sorted(threads, key=lambda t: t.get("updated_at", ""), reverse=True)
