"""Auth middleware — JWT verification. Entra ID only, no API keys."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as _tz
UTC = _tz.utc

import jwt
from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


def sign_token(payload: dict) -> str:
    """Create a JWT token."""
    data = {
        **payload,
        "exp": datetime.now(UTC) + timedelta(hours=settings.jwt_expiry_hours),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(data, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def is_entra_user(user: dict) -> bool:
    """True if the user logged in via Microsoft Entra ID.

    Used to gate email features that require Mail.Read/Mail.Send scopes —
    corporate tenant policies typically block those, but personal IMAP/SMTP
    with app passwords (Google, email+password registration) works fine.
    """
    uid = (user or {}).get("id", "")
    return not (
        uid.startswith("google-") or
        uid.startswith("email-") or
        uid.startswith("ext-")
    )


ENTRA_EMAIL_BLOCK_MESSAGE = (
    "Email integration isn't available for Microsoft work accounts due to "
    "tenant compliance policies on Mail.Read/Mail.Send permissions. "
    "To use email features in Lumen, sign in with a Google account or "
    "register with email + password instead. The rest of Lumen (chat, "
    "learning, scheduling, peers) works normally."
)


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency — extract and verify Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def verify_entra_token(id_token: str) -> dict:
    """Verify Microsoft Entra ID token using JWKS."""
    import httpx
    from jose import jwt as jose_jwt, JWTError

    jwks_url = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/discovery/v2.0/keys"
    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url)
        jwks = resp.json()

    try:
        header = jose_jwt.get_unverified_header(id_token)
        key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
        claims = jose_jwt.decode(
            id_token, key, algorithms=["RS256"],
            audience=settings.entra_client_id,
            issuer=f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0",
        )
        return claims
    except (JWTError, StopIteration) as e:
        raise HTTPException(status_code=401, detail=f"Invalid Entra token: {e}")
