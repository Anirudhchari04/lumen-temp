"""Google integration routes — OAuth (sign-in + connect) + Gmail/Drive proxies."""

from __future__ import annotations

import logging
import secrets
import uuid
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.auth import get_current_user, sign_token
from app.lumen.core import get_lumen, get_or_create_lumen
from app.agents.gmail_agent import (
    is_google_connected,
    is_gmail_connected,
    is_drive_connected,
    is_gcalendar_connected,
    save_google_config,
    disconnect_google,
    get_valid_google_token,
    list_inbox,
    get_message,
    search_gmail,
    send_gmail,
    summarize_message,
    GMAIL_SCOPE,
    DRIVE_SCOPE,
    CALENDAR_SCOPE,
)
from app.agents.gdrive_agent import (
    list_files as drive_list_files,
    read_file as drive_read_file,
    search_drive,
    create_doc as drive_create_doc,
    summarize_file as drive_summarize_file,
    append_to_doc as drive_append_to_doc,
    replace_doc_content as drive_replace_doc,
    find_replace_doc as drive_find_replace,
)
from app.agents.gcalendar_agent import (
    list_events as gcal_list_events,
    search_events as gcal_search_events,
    get_event as gcal_get_event,
    create_event as gcal_create_event,
    delete_event as gcal_delete_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["google"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

# Identity-only scopes — used for "Sign in with Google" on the login page.
# No Gmail/Drive/Calendar access is requested at sign-in; those are granted
# later, in-app, on first use (see the connect flow below).
IDENTITY_SCOPES = " ".join(["openid", "email", "profile"])

# Full scopes — requested only when the signed-in user explicitly connects
# Google for Gmail/Drive/Calendar access.
FULL_SCOPES = " ".join([
    "openid",
    "email",
    "profile",
    GMAIL_SCOPE,
    DRIVE_SCOPE,
    CALENDAR_SCOPE,
])

# In-memory CSRF state store (5-min TTL). For demo scale this is fine.
_oauth_states: dict[str, dict] = {}


def _get_google_oauth_config() -> tuple[str, str, str]:
    cid = settings.google_client_id or ""
    cs = settings.google_client_secret or ""
    ru = settings.google_redirect_uri or ""
    if not (cid and cs and ru):
        raise HTTPException(
            status_code=501,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI.",
        )
    return cid, cs, ru


# ── OAuth: authorize URL ─────────────────────────────────────────────────────

@router.get("/auth/google-authorize-url")
async def google_authorize_url(request: Request, mode: str = "always"):
    """Return a one-time-use OAuth URL the frontend can open in a popup.

    The flow is decided by whether the caller already has a Lumen JWT:
      - No JWT  → Sign-in (login page). Identity scopes only; no Gmail/Drive/
        Calendar access is requested here.
      - Has JWT → In-app Connect. Full Gmail/Drive/Calendar scopes. `mode`
        controls persistence:
          * "always" → offline access (refresh token kept) → stays connected.
          * "once"   → online access (no refresh token) → access expires (~1h),
            then Lumen re-prompts. Honours "this request only".
    """
    cid, _cs, ru = _get_google_oauth_config()

    # Optional existing user — presence of a valid JWT means "Connect" not "sign in".
    existing_user_id: str | None = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = pyjwt.decode(auth[7:], settings.jwt_secret,
                                    algorithms=[settings.jwt_algorithm])
            existing_user_id = payload.get("id")
        except Exception:
            pass

    is_connect = existing_user_id is not None
    consent_mode = "once" if (mode or "").lower() == "once" else "always"

    state = secrets.token_urlsafe(24)
    _oauth_states[state] = {
        "user_id": existing_user_id,
        "connect": is_connect,
        "mode": consent_mode,
    }

    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": ru,
        "scope": FULL_SCOPES if is_connect else IDENTITY_SCOPES,
        "state": state,
        "include_granted_scopes": "true",
    }
    if is_connect:
        if consent_mode == "always":
            # Persistent grant needs a refresh token → force consent + offline.
            params["prompt"] = "consent"
            params["access_type"] = "offline"
        else:
            # "once": online only, and DON'T force the consent screen — Google
            # auto-approves already-granted scopes, so re-prompting after the
            # one-time blocker fires is a quick, frictionless popup.
            params["access_type"] = "online"
    else:
        # Sign-in: lightweight account picker, no offline access.
        params["prompt"] = "select_account"
        params["access_type"] = "online"

    return {"url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


# ── OAuth: callback ──────────────────────────────────────────────────────────

class GoogleCallbackBody(BaseModel):
    code: str
    state: str


@router.post("/auth/google-callback")
async def google_callback(body: GoogleCallbackBody, request: Request):
    """Exchange the OAuth code for tokens.

    If the caller already has a Lumen JWT (cross-account Connect), persist tokens
    on the existing user. Otherwise (sign-in flow), create/get a `google-{sub}`
    user and return a freshly-minted Lumen JWT.
    """
    cid, cs, ru = _get_google_oauth_config()

    saved = _oauth_states.pop(body.state, None)
    if saved is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state token")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": body.code,
                "client_id": cid,
                "client_secret": cs,
                "redirect_uri": ru,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        logger.error(f"Google token exchange failed: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")

    tokens = resp.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token", "")
    scopes = tokens.get("scope", "")
    expires_in = int(tokens.get("expires_in", 3600))
    id_token = tokens.get("id_token", "")

    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token in Google response")

    # Get the user's email + sub from userinfo endpoint
    async with httpx.AsyncClient(timeout=15.0) as client:
        ui = await client.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {access_token}"})
    if ui.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Could not fetch Google userinfo: {ui.text}")
    info = ui.json()
    email = info.get("email", "")
    name = info.get("name", "")
    sub = info.get("sub", str(uuid.uuid4()))

    # Decide: sign-in (identity only) vs in-app Connect (persist Gmail/Drive/Calendar).
    existing_user_id = saved.get("user_id") if saved else None
    is_connect = bool(saved.get("connect")) if saved else False
    consent_mode = (saved.get("mode") if saved else "always") or "always"
    issued_token = None

    if existing_user_id:
        # In-app Connect — keep their identity, persist the Google tokens.
        target_user_id = existing_user_id
        lumen = await get_lumen(target_user_id)
        if not lumen:
            raise HTTPException(status_code=404, detail="Existing user not found")
        await save_google_config(
            user_id=target_user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=scopes,
            expires_in=expires_in,
            email=email,
            consent=consent_mode,
        )
    else:
        # Sign-in path — create/get google-{sub} user, issue a new JWT.
        # Identity only: do NOT persist Google tokens. Gmail/Drive/Calendar
        # access is granted later, in-app, on first use.
        target_user_id = f"google-{sub}"
        lumen = await get_or_create_lumen(target_user_id, name, email, tenant_id="google")
        issued_token = sign_token({"id": lumen["id"], "email": email, "name": name})

    return {
        "connected": is_connect,
        "email": email,
        "name": name,
        "scopes": scopes if is_connect else "",
        "consent": consent_mode if is_connect else None,
        # Only present on the sign-in path
        "token": issued_token,
        "user": {"id": target_user_id, "name": name, "email": email} if issued_token else None,
        "profileComplete": True if issued_token else None,
    }


# ── Connection status / disconnect ───────────────────────────────────────────

@router.get("/lumen/google/status")
async def google_status(current_user: dict = Depends(get_current_user)):
    lumen = await get_lumen(current_user["id"])
    if not is_google_connected(lumen):
        return {"connected": False, "gmail": False, "drive": False}
    cfg = lumen.get("google_config", {})
    return {
        "connected": True,
        "email": cfg.get("email", ""),
        "scopes": cfg.get("scopes", ""),
        "consent": cfg.get("consent", "always"),
        "gmail": is_gmail_connected(lumen),
        "drive": is_drive_connected(lumen),
        "calendar": is_gcalendar_connected(lumen),
    }


@router.post("/lumen/google/disconnect")
async def google_disconnect(current_user: dict = Depends(get_current_user)):
    ok = await disconnect_google(current_user["id"])
    return {"disconnected": ok}


# ── Gmail proxies ────────────────────────────────────────────────────────────

class GmailListBody(BaseModel):
    query: str = ""
    limit: int = 10


@router.post("/lumen/gmail/list")
async def gmail_list(body: GmailListBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    results = await list_inbox(token, body.query, limit=body.limit)
    return {"results": results}


class GmailReadBody(BaseModel):
    message_id: str


@router.post("/lumen/gmail/read")
async def gmail_read(body: GmailReadBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await get_message(token, body.message_id)


class GmailSearchBody(BaseModel):
    query: str
    limit: int = 10


@router.post("/lumen/gmail/search")
async def gmail_search_route(body: GmailSearchBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    results = await search_gmail(token, body.query, limit=body.limit)
    return {"results": results}


class GmailSendBody(BaseModel):
    to: str
    subject: str
    body: str
    reply_to_message_id: str | None = None


@router.post("/lumen/gmail/send")
async def gmail_send_route(body: GmailSendBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    lumen = await get_lumen(current_user["id"])
    sender_email = (lumen or {}).get("google_config", {}).get("email", "")
    result = await send_gmail(token, body.to, body.subject, body.body,
                                reply_to_msg_id=body.reply_to_message_id,
                                sender_email=sender_email)
    return result


class GmailSummarizeBody(BaseModel):
    message_id: str
    instruction: str = ""


@router.post("/lumen/gmail/summarize")
async def gmail_summarize_route(body: GmailSummarizeBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    summary = await summarize_message(token, body.message_id, body.instruction)
    return {"summary": summary}


# ── Drive proxies ────────────────────────────────────────────────────────────

class DriveListBody(BaseModel):
    query: str = ""
    limit: int = 10


@router.post("/lumen/drive/list")
async def drive_list(body: DriveListBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    files = await drive_list_files(token, body.query, limit=body.limit)
    return {"results": files}


class DriveReadBody(BaseModel):
    file_id: str


@router.post("/lumen/drive/read")
async def drive_read(body: DriveReadBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await drive_read_file(token, body.file_id)


class DriveSearchBody(BaseModel):
    query: str
    limit: int = 10


@router.post("/lumen/drive/search")
async def drive_search_route(body: DriveSearchBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    files = await search_drive(token, body.query, limit=body.limit)
    return {"results": files}


class DriveCreateBody(BaseModel):
    title: str
    content_lines: list[str] = []


@router.post("/lumen/drive/create")
async def drive_create(body: DriveCreateBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await drive_create_doc(token, body.title, body.content_lines)


class DocAppendBody(BaseModel):
    file_id: str
    content: str


@router.post("/lumen/drive/doc/append")
async def doc_append_route(body: DocAppendBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await drive_append_to_doc(token, body.file_id, body.content)


class DocReplaceBody(BaseModel):
    file_id: str
    content: str


@router.post("/lumen/drive/doc/replace")
async def doc_replace_route(body: DocReplaceBody, current_user: dict = Depends(get_current_user)):
    """DESTRUCTIVE — wipes the entire body of a Google Doc and inserts new content."""
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await drive_replace_doc(token, body.file_id, body.content)


class DocFindReplaceBody(BaseModel):
    file_id: str
    find: str
    replace: str
    match_case: bool = False


@router.post("/lumen/drive/doc/find-replace")
async def doc_find_replace_route(body: DocFindReplaceBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await drive_find_replace(token, body.file_id, body.find, body.replace, body.match_case)


class DriveSummarizeBody(BaseModel):
    file_id: str
    instruction: str = ""


@router.post("/lumen/drive/summarize")
async def drive_summarize_route(body: DriveSummarizeBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    summary = await drive_summarize_file(token, body.file_id, body.instruction)
    return {"summary": summary}


# ── Google Calendar proxies ──────────────────────────────────────────────────

class GcalListBody(BaseModel):
    days_ahead: int = 7
    limit: int = 20


@router.post("/lumen/gcalendar/list")
async def gcal_list(body: GcalListBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    events = await gcal_list_events(token, days_ahead=body.days_ahead, max_results=body.limit)
    return {"results": events}


class GcalSearchBody(BaseModel):
    query: str
    days_ahead: int = 90
    limit: int = 20


@router.post("/lumen/gcalendar/search")
async def gcal_search_route(body: GcalSearchBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    events = await gcal_search_events(token, body.query, days_ahead=body.days_ahead, max_results=body.limit)
    return {"results": events}


class GcalCreateBody(BaseModel):
    title: str
    start: str | None = None  # ISO datetime
    end: str | None = None
    description: str = ""
    location: str = ""
    attendees: list[str] = []
    all_day: bool = False
    natural_when: str | None = None  # e.g. "tomorrow at 3pm"


@router.post("/lumen/gcalendar/create")
async def gcal_create(body: GcalCreateBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")

    start = body.start
    end = body.end
    all_day = body.all_day

    if not start and body.natural_when:
        from app.agents.gcalendar_agent import parse_when
        parsed = parse_when(body.natural_when)
        if parsed:
            s, e, ad = parsed
            start, end, all_day = s.isoformat(), e.isoformat(), ad
    if not start:
        raise HTTPException(status_code=400, detail="Provide start (ISO) or natural_when")

    res = await gcal_create_event(
        token, title=body.title, start=start, end=end,
        description=body.description, location=body.location,
        attendees=body.attendees, all_day=all_day,
    )
    return res


class GcalDeleteBody(BaseModel):
    event_id: str


@router.post("/lumen/gcalendar/delete")
async def gcal_delete(body: GcalDeleteBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await gcal_delete_event(token, body.event_id)


class GcalGetBody(BaseModel):
    event_id: str


@router.post("/lumen/gcalendar/get")
async def gcal_get_route(body: GcalGetBody, current_user: dict = Depends(get_current_user)):
    token = await get_valid_google_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected")
    return await gcal_get_event(token, body.event_id)
