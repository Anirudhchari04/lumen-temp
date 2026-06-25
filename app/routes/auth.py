"""Auth routes — Entra ID + Google login."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lumen.core import get_or_create_lumen, get_lumen_profile
from app.auth import verify_entra_token, sign_token, get_current_user
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

# In-memory demo guest for unauthenticated access
DEMO_GUEST = {"id": "demo-guest", "name": "Demo Student", "email": "demo@lumen.local"}

# ── Device code sessions (in-memory, keyed by session_id) ────────────────────
# Each entry: { device_code, expires_at, interval, status, token/error }
_device_sessions: Dict[str, dict] = {}


# ── Auto-seed Graph token (JWT-secured, called by frontend after login) ───────

class GraphTokenSeedBody(BaseModel):
    access_token: str


@router.post("/graph-token")
async def seed_graph_token(body: GraphTokenSeedBody, current_user: dict = Depends(get_current_user)):
    """Receive a fresh Graph access token from the logged-in user's MSAL session.
    Called automatically by the frontend after every login and every 45 min.
    No manual token management needed.
    """
    from app.agents.graph_token_manager import set_access_token
    ok = await set_access_token(body.access_token)
    if ok:
        return {"ok": True}
    raise HTTPException(status_code=500, detail="Failed to store token")


@router.get("/graph-token-status")
async def graph_token_status(current_user: dict = Depends(get_current_user)):
    """Returns whether a valid Graph token is currently seeded on the backend."""
    from app.agents.graph_token_manager import get_graph_access_token
    token = await get_graph_access_token()
    return {"connected": bool(token)}


# ── External Graph token (college / personal Outlook account) ─────────────────

@router.post("/external-graph-token")
async def seed_external_graph_token(body: GraphTokenSeedBody, current_user: dict = Depends(get_current_user)):
    """Store a Graph access token from an external (college/personal) Microsoft account.
    Used by the comms agent to send/read email from a non-Entra account.
    """
    from app.agents.graph_token_manager import set_external_access_token
    info = await set_external_access_token(body.access_token)
    return {"ok": True, "account": info.get("account", ""), "email": info.get("email", "")}


@router.get("/external-graph-token-status")
async def external_graph_token_status(current_user: dict = Depends(get_current_user)):
    """Returns whether a valid external Graph token is seeded, plus the account email."""
    from app.agents.graph_token_manager import get_external_token_info
    info = get_external_token_info()
    return info


@router.delete("/external-graph-token")
async def clear_external_graph_token(current_user: dict = Depends(get_current_user)):
    """Remove the external Graph token (disconnect external account)."""
    from app.agents.graph_token_manager import clear_external_access_token
    clear_external_access_token()
    return {"ok": True}


# ── Outlook token-paste login (no app registration required) ─────────────────

class OutlookTokenLoginBody(BaseModel):
    access_token: str  # Graph access token pasted from Graph Explorer


@router.post("/outlook-token-login")
async def outlook_token_login(body: OutlookTokenLoginBody):
    """Login using a raw Microsoft Graph access token (e.g. from Graph Explorer).
    Works for any Microsoft account including college/personal Outlook.
    No registered app or admin consent needed — user obtains token manually.
    """
    import httpx
    from app.agents.graph_token_manager import set_external_access_token

    # Validate token by calling /me
    async with httpx.AsyncClient(timeout=10) as client:
        me_resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {body.access_token}"},
        )

    if not me_resp.is_success:
        raise HTTPException(status_code=401, detail="Invalid or expired token — could not fetch profile from Microsoft Graph")

    me = me_resp.json()
    email = me.get("mail") or me.get("userPrincipalName") or me.get("id", "")
    name  = me.get("displayName") or email.split("@")[0]
    oid   = me.get("id", str(uuid.uuid4()))

    # Store as external Graph token for comms agent
    await set_external_access_token(body.access_token)

    # Create or get Lumen account
    lumen = await get_or_create_lumen(f"ext-{oid}", name, email, tenant_id="external")
    jwt_token = sign_token({"id": lumen["id"], "email": email, "name": name})

    return {
        "token": jwt_token,
        "user": {"id": lumen["id"], "name": name, "email": email},
        "lumen_id": lumen["lumen_id"],
        "profileComplete": True,
    }


# ── Outlook Device Code Flow ──────────────────────────────────────────────────

DEVICE_CODE_SCOPES = "User.Read Mail.Read Calendars.Read offline_access openid email profile"


@router.post("/outlook-device-code/start")
async def outlook_device_code_start():
    """Initiate the Microsoft device code flow.
    Returns user_code (shown to user) and session_id (used to poll for result).
    Requires EXTERNAL_OUTLOOK_CLIENT_ID to be set.
    """
    if not settings.external_outlook_client_id:
        raise HTTPException(
            status_code=503,
            detail="EXTERNAL_OUTLOOK_CLIENT_ID not configured. Register a public client app and set this env var."
        )

    import httpx

    client_id = settings.external_outlook_client_id
    device_code_url = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"

    async with httpx.AsyncClient(timeout=15) as hc:
        resp = await hc.post(device_code_url, data={
            "client_id": client_id,
            "scope": DEVICE_CODE_SCOPES,
        })

    if not resp.is_success:
        logger.error("Device code request failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(status_code=400, detail=f"Microsoft error: {resp.text}")

    data = resp.json()
    session_id = secrets.token_urlsafe(16)
    _device_sessions[session_id] = {
        "device_code": data["device_code"],
        "interval": int(data.get("interval", 5)),
        "expires_at": time.time() + int(data.get("expires_in", 900)),
        "status": "pending",
        "client_id": client_id,
    }

    return {
        "session_id": session_id,
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", "https://microsoft.com/devicelogin"),
        "expires_in": int(data.get("expires_in", 900)),
    }


@router.get("/outlook-device-code/poll/{session_id}")
async def outlook_device_code_poll(session_id: str):
    """Poll for device code completion.
    Returns status: pending | complete | expired | error
    When complete, returns lumen JWT + user info.
    """
    sess = _device_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    if time.time() > sess["expires_at"]:
        _device_sessions.pop(session_id, None)
        return {"status": "expired"}

    if sess["status"] == "complete":
        result = {**sess["result"]}
        _device_sessions.pop(session_id, None)
        return {"status": "complete", **result}

    if sess["status"] == "error":
        err = sess.get("error", "unknown")
        _device_sessions.pop(session_id, None)
        return {"status": "error", "error": err}

    # Actually poll Microsoft
    import httpx
    from app.agents.graph_token_manager import set_external_access_token

    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=15) as hc:
        resp = await hc.post(token_url, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": sess["client_id"],
            "device_code": sess["device_code"],
        })

    data = resp.json()
    error = data.get("error")

    if error == "authorization_pending":
        return {"status": "pending"}
    if error in ("slow_down",):
        sess["interval"] = min(sess["interval"] + 5, 30)
        return {"status": "pending"}
    if error:
        sess["status"] = "error"
        sess["error"] = error
        return {"status": "error", "error": error}

    # Success — exchange for user info
    access_token = data.get("access_token")
    if not access_token:
        return {"status": "error", "error": "no_token"}

    async with httpx.AsyncClient(timeout=10) as hc:
        me_resp = await hc.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if not me_resp.is_success:
        sess["status"] = "error"
        sess["error"] = "graph_me_failed"
        return {"status": "error", "error": "Could not fetch profile"}

    me = me_resp.json()
    email = me.get("mail") or me.get("userPrincipalName") or me.get("id", "")
    name = me.get("displayName") or email.split("@")[0]
    oid = me.get("id", str(uuid.uuid4()))

    await set_external_access_token(access_token)
    lumen = await get_or_create_lumen(f"ext-{oid}", name, email, tenant_id="external")
    jwt_token = sign_token({"id": lumen["id"], "email": email, "name": name})

    result = {
        "token": jwt_token,
        "user": {"id": lumen["id"], "name": name, "email": email},
    }
    sess["status"] = "complete"
    sess["result"] = result
    return {"status": "complete", **result}


@router.get("/outlook-device-code/config")
async def outlook_device_code_config():
    """Returns whether device code sign-in is available (client_id configured)."""
    return {"available": bool(settings.external_outlook_client_id)}


class EntraLoginBody(BaseModel):
    idToken: str



@router.post("/entra-login")
async def entra_login(body: EntraLoginBody):
    """Validate Entra ID token, create Lumen, return JWT."""
    try:
        claims = await verify_entra_token(body.idToken)
    except Exception as exc:
        logger.error("Entra token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Entra ID token")

    name = claims.get("name", "")
    email = claims.get("preferred_username") or claims.get("email") or ""
    oid = claims.get("oid", str(uuid.uuid4()))
    tid = claims.get("tid", "")

    # Create or get Lumen
    lumen = await get_or_create_lumen(oid, name, email, tenant_id=tid)

    token = sign_token({"id": lumen["id"], "email": email, "name": name})

    return {
        "token": token,
        "user": {"id": lumen["id"], "name": name, "email": email},
        "lumen_id": lumen["lumen_id"],
        "profileComplete": True,
    }


@router.get("/profile")
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Get current user's Lumen profile."""
    profile = await get_lumen_profile(current_user["id"])
    if not profile:
        return {**current_user, "profileComplete": True}
    profile["profileComplete"] = True
    return profile


@router.delete("/account")
async def delete_my_account(current_user: dict = Depends(get_current_user)):
    """Self-service: delete the current user's profile + threads + graph tokens.

    The user's JWT is immediately invalid (record no longer exists). Frontend
    should signOut + redirect to /login after a successful call.
    """
    from app.db.cosmos import delete_lumen as cosmos_delete_lumen
    user_id = current_user["id"]
    deleted = await cosmos_delete_lumen(user_id)
    logger.info(f"Self-delete: user={user_id} email={current_user.get('email')} deleted={deleted}")
    return {"deleted": deleted, "user_id": user_id}


# NOTE: /auth/demo-token has been removed. All access now requires a real
# Entra ID or Google sign-in.


# ── Google OAuth Login ───────────────────────────────────────

class GoogleLoginBody(BaseModel):
    credential: str  # Google ID token (JWT from Google Sign-In)


async def verify_google_token(id_token: str) -> dict:
    """Verify a Google ID token using Google's public keys."""
    import httpx
    from jose import jwt as jose_jwt, JWTError

    # Fetch Google's public keys
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.googleapis.com/oauth2/v3/certs")
        jwks = resp.json()

    try:
        header = jose_jwt.get_unverified_header(id_token)
        key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
        claims = jose_jwt.decode(
            id_token, key, algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=["https://accounts.google.com", "accounts.google.com"],
        )
        return claims
    except (JWTError, StopIteration) as e:
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {e}")


@router.post("/google-login")
async def google_login(body: GoogleLoginBody):
    """Validate Google ID token, create Lumen, return JWT."""
    if not settings.google_client_id:
        raise HTTPException(status_code=501, detail="Google login not configured. Set GOOGLE_CLIENT_ID env var.")

    try:
        claims = await verify_google_token(body.credential)
    except Exception as exc:
        logger.error("Google token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Google ID token")

    name = claims.get("name", "")
    email = claims.get("email", "")
    sub = claims.get("sub", str(uuid.uuid4()))

    # Create or get Lumen (use Google sub as user ID, prefixed to avoid collision)
    user_id = f"google-{sub}"
    lumen = await get_or_create_lumen(user_id, name, email, tenant_id="google")

    token = sign_token({"id": lumen["id"], "email": email, "name": name})

    return {
        "token": token,
        "user": {"id": lumen["id"], "name": name, "email": email},
        "lumen_id": lumen["lumen_id"],
        "profileComplete": True,
    }


@router.get("/google-client-id")
async def google_client_id():
    """Return the Google Client ID for the frontend (public, not secret)."""
    return {"clientId": settings.google_client_id or None}


# ── Email/Password Auth (self-managed, no third-party) ─────────────────────────

class RegisterBody(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(body: RegisterBody):
    """Register a new account with email and password.

    Password is hashed with bcrypt. User ID is deterministic from email hash
    so repeated registrations of the same email don't create duplicates.
    """
    import bcrypt
    import hashlib

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    name = (body.name or email.split("@")[0]).strip()

    # Create stable user ID from email hash
    user_id = "email-" + hashlib.sha256(email.encode()).hexdigest()[:16]

    # Check if already exists
    existing = await get_or_create_lumen(user_id, "", "")
    if existing.get("password_hash"):
        raise HTTPException(status_code=409, detail="Email already registered")

    # Hash password
    password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt(rounds=12)).decode()

    # Create Lumen with password
    lumen = await get_or_create_lumen(user_id, name, email, tenant_id="email")
    lumen["password_hash"] = password_hash

    from app.lumen.core import save_lumen
    await save_lumen(lumen)

    logger.info(f"Registered new user: {email}")

    # Auto-login after registration
    token = sign_token({"id": lumen["id"], "email": email, "name": name})
    return {
        "token": token,
        "user": {"id": lumen["id"], "name": name, "email": email},
        "lumen_id": lumen["lumen_id"],
        "profileComplete": True,
    }


@router.post("/login")
async def login(body: LoginBody):
    """Login with email and password.

    Returns JWT on success.
    """
    import bcrypt
    import hashlib

    email = body.email.strip().lower()
    user_id = "email-" + hashlib.sha256(email.encode()).hexdigest()[:16]

    # Get lumen
    lumen = await get_or_create_lumen(user_id, "", "")

    if not lumen.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Verify password
    try:
        if not bcrypt.checkpw(body.password.encode(), lumen["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="Invalid email or password")
    except Exception as e:
        logger.warning(f"Password check failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Issue JWT
    token = sign_token({
        "id": lumen["id"],
        "email": lumen.get("email", email),
        "name": lumen.get("name", "")
    })

    logger.info(f"Logged in user: {email}")

    return {
        "token": token,
        "user": {
            "id": lumen["id"],
            "name": lumen.get("name", ""),
            "email": lumen.get("email", "")
        },
        "lumen_id": lumen.get("lumen_id", ""),
        "profileComplete": bool(lumen.get("name")),
    }
