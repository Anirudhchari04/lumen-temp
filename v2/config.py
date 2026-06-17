"""Lumen v2 configuration.

Reads the SAME .env / App Service settings as v1 by importing v1's already-loaded
`settings` object — v2 never defines its own copy of a credential and never writes
secrets anywhere. The only v2-specific knobs are optional, documented in
v2/.env.example, and have safe defaults so v2 runs without any new config.
"""

from __future__ import annotations

import os

from app.config import settings as _v1  # v1's loaded pydantic settings (reads .env)

# ── Reused v1 credentials / endpoints (read-only references) ────────────────
AZURE_OPENAI_ENDPOINT = _v1.azure_openai_endpoint
AZURE_OPENAI_DEPLOYMENT = _v1.azure_openai_deployment
AZURE_OPENAI_API_VERSION = _v1.azure_openai_api_version
AZURE_MI_CLIENT_ID = _v1.azure_managed_identity_client_id
COSMOS_ENDPOINT = _v1.cosmos_endpoint
COSMOS_DATABASE = _v1.cosmos_database

# ── v2-specific (optional) — see v2/.env.example ────────────────────────────
# New Cosmos container for v2 sessions. Never one of v1's containers.
V2_SESSIONS_CONTAINER = os.environ.get("LUMEN_V2_CONTAINER", "lumen_v2_sessions")
# Hard cap on MagenticOne orchestration turns per chat (safety bound).
V2_MAX_TURNS = int(os.environ.get("LUMEN_V2_MAX_TURNS", "12"))
# Allow disabling v2 mount without code changes (router still imports fine).
V2_ENABLED = os.environ.get("LUMEN_V2_ENABLED", "1").lower() not in ("0", "false", "no")
