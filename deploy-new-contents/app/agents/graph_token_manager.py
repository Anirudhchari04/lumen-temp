"""Graph Token Manager — Microsoft Graph access tokens for backend Graph calls.

The frontend (MSAL) acquires tokens after the one-time login consent and seeds
the backend every 45 minutes via POST /auth/graph-token.
The backend stores the token in memory — no manual token management needed.

Usage:
    token = await get_graph_access_token()
    # Returns the current in-memory token if valid, else tries MSAL silent refresh.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Optional

import msal

logger = logging.getLogger(__name__)

# MSAL app registration (same client used in frontend)
CLIENT_ID = "baabcd68-1c44-44bb-ba2e-c6bbc77b216d"
TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Files.Read",
    "offline_access",
]

# In-memory cache (loaded from Cosmos or env var at startup)
_token_cache = msal.SerializableTokenCache()
_msal_app: Optional[msal.PublicClientApplication] = None

# Direct access token (seeded by frontend MSAL every 45 min — in memory only)
_direct_token: Optional[str] = None
_direct_token_exp: int = 0

# External access token (college / personal Outlook — pasted by user)
_external_token: Optional[str] = None
_external_token_exp: int = 0
_external_token_email: str = ""


def _get_msal_app() -> msal.PublicClientApplication:
    global _msal_app
    if _msal_app is None:
        _msal_app = msal.PublicClientApplication(
            CLIENT_ID,
            authority=AUTHORITY,
            token_cache=_token_cache,
        )
    return _msal_app


# ── Persistence (Cosmos DB preferred, env var fallback) ──────────────────────

_CACHE_DOC_ID = "graph_token_cache"
_CACHE_CONTAINER = "graph_tokens"


async def _load_cache_from_cosmos() -> bool:
    """Load serialized token cache from Cosmos DB. Returns True on success."""
    try:
        from app.db.cosmos import _containers
        container = _containers.get("graph_tokens")
        if not container:
            return False
        doc = await container.read_item(_CACHE_DOC_ID, partition_key=_CACHE_DOC_ID)
        serialized = doc.get("cache")
        if serialized:
            _token_cache.deserialize(serialized)
            logger.info("Graph token cache loaded from Cosmos")
            return True
    except Exception as e:
        logger.debug(f"Cosmos cache load failed: {e}")
    return False


async def _save_cache_to_cosmos():
    """Persist serialized token cache to Cosmos DB."""
    if not _token_cache.has_state_changed:
        return
    try:
        from app.db.cosmos import _containers
        container = _containers.get("graph_tokens")
        if not container:
            return
        await container.upsert_item({
            "id": _CACHE_DOC_ID,
            "partitionKey": _CACHE_DOC_ID,
            "cache": _token_cache.serialize(),
        })
        logger.debug("Graph token cache saved to Cosmos")
    except Exception as e:
        logger.warning(f"Cosmos cache save failed: {e}")


def _load_cache_from_env():
    """Load serialized token cache from GRAPH_TOKEN_CACHE env var (fallback)."""
    raw = os.environ.get("GRAPH_TOKEN_CACHE")
    if raw:
        try:
            _token_cache.deserialize(raw)
            logger.info("Graph token cache loaded from env var")
            return True
        except Exception as e:
            logger.warning(f"Env cache load failed: {e}")
    return False


# ── Public API ───────────────────────────────────────────────────────────────

_cache_loaded = False


async def _ensure_cache_loaded():
    global _cache_loaded
    if _cache_loaded:
        return
    loaded = await _load_cache_from_cosmos()
    if not loaded:
        _load_cache_from_env()
    _cache_loaded = True


def _decode_jwt_exp(token: str) -> int:
    """Extract exp claim from JWT without verification."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        return int(payload.get("exp", 0))
    except Exception:
        return 0


async def set_access_token(access_token: str) -> bool:
    """Store a Graph access token in memory. Re-seeded by frontend every 45 min."""
    global _direct_token, _direct_token_exp
    _direct_token = access_token
    _direct_token_exp = _decode_jwt_exp(access_token)
    logger.info("Graph access token updated in memory")
    return True


async def get_graph_access_token() -> Optional[str]:
    """Get a valid Graph access token.
    Priority: (1) direct stored token if not expired, (2) MSAL silent refresh.
    Returns None if no valid token available."""
    await _ensure_cache_loaded()

    # 1. Direct stored access token (from /admin/graph-token with access_token field)
    if _direct_token and _direct_token_exp > int(time.time()) + 60:
        return _direct_token

    # 2. MSAL silent refresh
    app = _get_msal_app()
    accounts = app.get_accounts()
    if not accounts:
        logger.debug("No accounts in Graph token cache")
        return None

    result = app.acquire_token_silent(
        ["https://graph.microsoft.com/.default"],
        account=accounts[0],
    )
    if result and "access_token" in result:
        await _save_cache_to_cosmos()
        return result["access_token"]

    logger.warning(f"Silent token acquisition failed: {result.get('error_description', 'unknown') if result else 'None'}")
    return None


async def seed_refresh_token(refresh_token: str) -> bool:
    """Bootstrap the token cache from a refresh token.
    Call this once — after that, get_graph_access_token() auto-refreshes.
    """
    await _ensure_cache_loaded()
    app = _get_msal_app()

    # Use the refresh token to get a new access token and populate the cache
    result = app.acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)
    if "access_token" in result:
        await _save_cache_to_cosmos()
        logger.info(f"Graph token cache seeded for account: {result.get('id_token_claims', {}).get('preferred_username', '?')}")
        return True

    logger.error(f"Failed to seed refresh token: {result.get('error_description', result)}")
    return False


def clear_cache():
    """Clear the in-memory token cache."""
    global _msal_app, _cache_loaded
    _token_cache.deserialize("{}")
    _msal_app = None
    _cache_loaded = False


# ── External account token (college / personal Outlook) ──────────────────────

def _decode_jwt_email(token: str) -> str:
    """Extract preferred_username / upn / email from JWT payload."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        return (
            payload.get("preferred_username")
            or payload.get("upn")
            or payload.get("email")
            or payload.get("unique_name")
            or ""
        )
    except Exception:
        return ""


async def set_external_access_token(access_token: str) -> dict:
    """Store an external (college/personal) Graph access token in memory."""
    global _external_token, _external_token_exp, _external_token_email
    _external_token = access_token
    _external_token_exp = _decode_jwt_exp(access_token)
    _external_token_email = _decode_jwt_email(access_token)
    logger.info(f"External Graph token set for account: {_external_token_email or 'unknown'}")
    return {"account": _external_token_email, "email": _external_token_email}


def get_external_token_info() -> dict:
    """Return status of the external token."""
    now = int(time.time())
    connected = bool(_external_token and _external_token_exp > now + 60)
    return {
        "connected": connected,
        "email": _external_token_email if connected else "",
        "expires_at": _external_token_exp if connected else 0,
    }


async def get_external_graph_access_token() -> Optional[str]:
    """Return the external Graph token if valid, else None."""
    if _external_token and _external_token_exp > int(time.time()) + 60:
        return _external_token
    return None


def clear_external_access_token():
    """Remove the external Graph token."""
    global _external_token, _external_token_exp, _external_token_email
    _external_token = None
    _external_token_exp = 0
    _external_token_email = ""
    logger.info("External Graph token cleared")
