"""Lumen API — Endpoints that TAs call to read/write student state."""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.lumen.core import (
    get_lumen_profile, get_lumen_state, update_progress,
    get_or_create_lumen, get_all_lumens, get_lumen, save_lumen,
)
from app.middleware.auth import get_current_user
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["lumen"])


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/usage/tokens")
async def lumen_token_usage(current_user: dict = Depends(get_current_user)):
    """Return token usage with source breakdown.

    Includes overall totals (session/today/week/lifetime) and per-source splits,
    e.g. lumen, router, notion, drive, gmail, arxiv, communication, calendar.
    """
    from app.lumen.token_tracker import get_full_usage
    from app.lumen.pricing import annotate_usage, build_recommendations
    usage = await get_full_usage(current_user["id"])
    annotate_usage(usage)
    usage["recommendations"] = build_recommendations(usage)
    return usage


@router.post("/usage/tokens/reset-session")
async def lumen_token_usage_reset(current_user: dict = Depends(get_current_user)):
    """Reset only the per-session counter. Lifetime is unaffected."""
    from app.lumen.token_tracker import reset_session
    reset_session(current_user["id"])
    return {"ok": True}


@router.get("/usage/tokens/all")
async def lumen_token_usage_all():
    """Admin view: token usage across all users and agents in one spot.

    Returns a summary with per-user breakdown and aggregate totals by source.
    """
    from app.lumen.token_tracker import get_full_usage
    from app.lumen.pricing import cost_usd, aggregate_cost
    all_lumens = await get_all_lumens()
    users = []
    aggregate_by_source: dict = {}
    aggregate_total = 0

    for lumen_entry in all_lumens:
        uid = lumen_entry["id"]
        usage = await get_full_usage(uid)
        lifetime = usage.get("lifetime", {})
        by_source = usage.get("lifetime_by_source", {})
        user_total = lifetime.get("total", 0)
        aggregate_total += user_total

        user_cost = 0.0
        for src, cnt in by_source.items():
            c = cost_usd(cnt.get("prompt", 0), cnt.get("completion", 0), source=src)
            cnt["cost_usd"] = round(c, 6)
            user_cost += c
            agg = aggregate_by_source.setdefault(src, {"total": 0, "prompt": 0, "completion": 0, "calls": 0})
            agg["total"] += cnt.get("total", 0)
            agg["prompt"] += cnt.get("prompt", 0)
            agg["completion"] += cnt.get("completion", 0)
            agg["calls"] += cnt.get("calls", 0)

        users.append({
            "user_id": uid,
            "name": lumen_entry.get("name", ""),
            "lifetime_total": user_total,
            "lifetime_calls": lifetime.get("calls", 0),
            "lifetime_cost_usd": round(user_cost, 6),
            "by_source": by_source,
        })

    for src, agg in aggregate_by_source.items():
        agg["cost_usd"] = round(cost_usd(agg["prompt"], agg["completion"], source=src), 6)

    return {
        "aggregate_total_tokens": aggregate_total,
        "aggregate_cost_usd": round(aggregate_cost(aggregate_by_source), 6),
        "aggregate_by_source": aggregate_by_source,
        "users": users,
    }


@router.get("/features")
async def lumen_features():
    """Public capability + changelog feed. No auth needed."""
    from app.lumen.features import all_features, features_since
    return {
        "all": [f._asdict() for f in all_features()],
        "recent": [f._asdict() for f in features_since(days=21)],
    }


@router.get("/profile")
async def lumen_profile(current_user: dict = Depends(get_current_user)):
    """Owner reads their own Lumen profile (includes private fields)."""
    await get_or_create_lumen(current_user["id"], current_user.get("name", ""), current_user.get("email", ""))
    lumen = await get_lumen(current_user["id"])
    if not lumen:
        raise HTTPException(status_code=404, detail="Lumen not found")
    return {
        "id": lumen["id"],
        "lumen_id": lumen["lumen_id"],
        "name": lumen.get("name", ""),
        "bio": lumen.get("bio", ""),
        "expertise": lumen.get("expertise", ""),
        "interests": lumen.get("interests", ""),
        "dob": lumen.get("dob", ""),
        "address": lumen.get("address", ""),
        "occupation": lumen.get("occupation", ""),
        "phone": lumen.get("phone", ""),
        "visibility": lumen.get("visibility", {}),
        "social": lumen.get("social", {"discoverable": True, "share_progress": True}),
        "preferences": lumen.get("preferences", {}),
    }


class ProfileEdit(BaseModel):
    name: str | None = None
    bio: str | None = None
    expertise: str | None = None
    interests: str | None = None
    dob: str | None = None
    address: str | None = None
    occupation: str | None = None
    phone: str | None = None
    visibility: dict | None = None
    preferences: dict | None = None


@router.put("/profile")
async def update_lumen_profile(body: ProfileEdit, current_user: dict = Depends(get_current_user)):
    """Owner edits their own Lumen profile (name, bio, private info, visibility, prefs)."""
    lumen = await get_or_create_lumen(
        current_user["id"], current_user.get("name", ""), current_user.get("email", ""))
    if body.name is not None:       lumen["name"] = body.name[:120]
    if body.bio is not None:        lumen["bio"] = body.bio[:500]
    if body.expertise is not None:  lumen["expertise"] = body.expertise[:200]
    if body.interests is not None:  lumen["interests"] = body.interests[:200]
    if body.dob is not None:        lumen["dob"] = body.dob[:40]
    if body.address is not None:    lumen["address"] = body.address[:240]
    if body.occupation is not None: lumen["occupation"] = body.occupation[:120]
    if body.phone is not None:      lumen["phone"] = body.phone[:40]
    if body.visibility is not None:
        allowed = {"bio", "expertise", "interests", "dob", "address", "occupation", "phone"}
        vis = lumen.get("visibility", {}) or {}
        for k, v in body.visibility.items():
            if k in allowed and v in ("public", "private"):
                vis[k] = v
        lumen["visibility"] = vis
    if body.preferences is not None:
        base = lumen.get("preferences", {}) or {}
        base.update({k: v for k, v in body.preferences.items() if v is not None})
        lumen["preferences"] = base
    await save_lumen(lumen)
    return await update_lumen_profile_response(current_user)


async def update_lumen_profile_response(current_user: dict):
    lumen = await get_lumen(current_user["id"])
    return {
        "id": lumen["id"], "lumen_id": lumen["lumen_id"],
        "name": lumen.get("name", ""), "bio": lumen.get("bio", ""),
        "expertise": lumen.get("expertise", ""), "interests": lumen.get("interests", ""),
        "dob": lumen.get("dob", ""), "address": lumen.get("address", ""),
        "occupation": lumen.get("occupation", ""), "phone": lumen.get("phone", ""),
        "visibility": lumen.get("visibility", {}), "preferences": lumen.get("preferences", {}),
    }

async def lumen_state(ta_id: str | None = None, current_user: dict = Depends(get_current_user)):
    """TA reads learning state. Pass ?ta_id=X for TA-specific view with cross-TA context."""
    await get_or_create_lumen(current_user["id"], current_user.get("name", ""), current_user.get("email", ""))
    state = await get_lumen_state(current_user["id"], ta_id)
    if not state:
        raise HTTPException(status_code=404, detail="Lumen not found")
    return state


@router.get("/discovery")
async def lumen_discovery(current_user: dict = Depends(get_current_user)):
    """List all Lumens on the network."""
    return await get_all_lumens()


# ── Voice Live WebSocket Proxy ───────────────────────────────────────────────
# Proxies the browser WebSocket to Azure AI Voice Live API.
# The browser cannot call Voice Live directly (needs AAD Bearer token
# that we can't expose client-side). This backend fetches the token and
# acts as a transparent relay.

@router.websocket("/voice-live-ws")
async def voice_live_proxy(
    websocket: WebSocket,
    token: str = Query(None),
    foundry_token: str = Query(None),
):
    """WebSocket proxy: browser <-> Azure AI Voice Live API.

    foundry_token: optional Azure AD access token for https://ai.azure.com
    acquired by the client via MSAL.  When provided the backend skips the
    managed-identity credential and uses it directly, which works even when
    the App Service managed identity hasn't been granted a role on anirfoundry.
    """
    # Light auth check — just verify the token is a known JWT
    if not token:
        await websocket.close(code=1008, reason="No auth token")
        return

    try:
        import jose.jwt as jose_jwt
        claims = jose_jwt.get_unverified_claims(token)
        user_id = claims.get("oid") or claims.get("sub") or "unknown"
    except Exception:
        # Fall back: treat any non-empty token as valid (JWT may be self-signed)
        user_id = "unknown"

    await websocket.accept()

    # Resolve AAD token for Azure AI Foundry —————————————————————————————
    # Prefer the user-supplied token (acquired via MSAL in the browser).
    # Fall back to managed identity if the client didn't provide one.
    if foundry_token:
        aad_token = foundry_token
        logger.info("Voice Live: using client-supplied AI Foundry token")
    else:
        try:
            from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
            if settings.azure_managed_identity_client_id:
                credential = ManagedIdentityCredential(
                    client_id=settings.azure_managed_identity_client_id
                )
            else:
                credential = DefaultAzureCredential()
            aad = await credential.get_token("https://ai.azure.com/.default")
            aad_token = aad.token
            await credential.close()
            logger.info("Voice Live: using managed identity token")
        except Exception as e:
            logger.error(f"Voice Live: failed to get AAD token: {e}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "error": {"code": "auth_failed", "message": str(e)}
            }))
            await websocket.close()
            return

    # Build Voice Live endpoint — try multiple api-versions in order
    host = settings.foundry_endpoint.replace("https://", "").rstrip("/")
    project = settings.foundry_project  # e.g. proj-anirfoundry

    # Correct param names: agent-name and agent-project-name (dashes, not underscores)
    _API_VERSIONS = [
        "2025-05-01-preview",
        "2025-05-15-preview",
    ]

    try:
        async with aiohttp.ClientSession() as session:
            # Try each api-version until one connects
            vl_ws = None
            connected_version = None
            last_handshake_err = None

            for api_ver in _API_VERSIONS:
                vl_url = (
                    f"wss://{host}/voice-live/realtime"
                    f"?api-version={api_ver}"
                    f"&agent-name=voice-live-lumen"
                    f"&agent-project-name={project}"
                )
                try:
                    vl_ws = await session.ws_connect(
                        vl_url,
                        headers={"Authorization": f"Bearer {aad_token}"},
                        timeout=aiohttp.ClientTimeout(connect=10, total=3600),
                    )
                    connected_version = api_ver
                    logger.info(f"Voice Live connected: user={user_id} api-version={api_ver}")
                    break
                except aiohttp.WSServerHandshakeError as e:
                    logger.warning(
                        f"Voice Live handshake failed: api-version={api_ver} "
                        f"status={e.status} msg={e.message}"
                    )
                    last_handshake_err = e
                except aiohttp.ClientConnectorError as e:
                    logger.warning(f"Voice Live connector error: api-version={api_ver} err={e}")
                    last_handshake_err = e
                    break  # network error — no point trying other versions

            if vl_ws is None:
                err_detail = (
                    f"status={last_handshake_err.status} msg={last_handshake_err.message}"
                    if hasattr(last_handshake_err, "status")
                    else str(last_handshake_err)
                )
                logger.error(f"Voice Live: all api-versions failed. Last error: {err_detail}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": {
                        "code": "connection_failed",
                        "message": f"Voice Live API unavailable ({err_detail}). Falling back to text mode.",
                    },
                }))
                return

            logger.info(f"Voice Live API active: user={user_id} version={connected_version}")

            async def browser_to_foundry():
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await vl_ws.send_str(msg)
                except WebSocketDisconnect:
                    logger.info(f"Voice Live: browser closed (user={user_id})")
                except Exception as e:
                    logger.warning(f"Voice Live: browser→foundry relay error: {e}")

            async def foundry_to_browser():
                # Log the first few message types so agent-mode quirks are
                # easy to diagnose from App Service logs.
                msgs_seen = 0
                try:
                    async for msg in vl_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if msgs_seen < 5:
                                try:
                                    parsed = json.loads(msg.data)
                                    logger.info(
                                        f"Voice Live ←: {parsed.get('type','?')} "
                                        f"(user={user_id})"
                                    )
                                except Exception:
                                    pass
                                msgs_seen += 1
                            await websocket.send_text(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            close_info = ""
                            if vl_ws.close_code is not None:
                                close_info = (
                                    f" code={vl_ws.close_code} "
                                    f"reason={vl_ws.exception()!r}"
                                )
                            logger.warning(
                                f"Voice Live: upstream closed{close_info} "
                                f"(user={user_id})"
                            )
                            break
                except Exception as e:
                    logger.warning(f"Voice Live: foundry→browser relay error: {e}")

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_foundry()),
                    asyncio.create_task(foundry_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            await vl_ws.close()

    except Exception as e:
        logger.error(f"Voice Live proxy error: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "error": {"code": "proxy_error", "message": str(e)}
            }))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
