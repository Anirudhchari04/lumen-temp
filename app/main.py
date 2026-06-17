"""Lumen Demo — FastAPI Application.

Demonstrates a Lumen (persistent personal agent) interacting with
multiple mock Teaching Assistants. All auth via Entra ID, no API keys.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("lumen")


def _block_entra_email(current_user: dict) -> None:
    """No-op gate — kept for backward compatibility.

    Previously blocked Entra users from /lumen/email/* (IMAP) endpoints, but we
    now let them try — they'll see a connection-failed error if their tenant
    blocks app-password IMAP, which is informative enough. Entra users primarily
    use the WorkIQ MCP path (/lumen/comm/send-workiq) anyway.
    """
    return  # Allow everyone through; route choice is enforced by the comms agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🌟 Lumen Demo starting...")
    logger.info(f"   OpenAI: {settings.azure_openai_endpoint} (Entra ID, no API keys)")
    logger.info(f"   Model: {settings.azure_openai_deployment}")
    logger.info("   Auth: Entra ID only")

    # JWT secret strength check — HS256 needs >= 32 bytes of entropy to be safe.
    # The current default 'lumen-demo-secret' is 17 chars; flag loudly so a real
    # secret gets set in Azure Portal → lumen-demo → Configuration.
    if len(settings.jwt_secret) < 32:
        logger.warning(
            "⚠️  JWT_SECRET is %d chars — must be ≥ 32 for HS256 to be secure. "
            "Set a 32+ char random string in Azure Portal → lumen-demo → "
            "Configuration → Application settings → JWT_SECRET.",
            len(settings.jwt_secret),
        )

    # Auto-derive self base URL for internal A2A HTTP self-calls.
    # On Azure App Service, WEBSITE_HOSTNAME is set (e.g. "lumen-demo.azurewebsites.net").
    # Locally, fall back to http://localhost:{port}.
    import os as _os
    if not settings.app_base_url:
        _host = _os.environ.get("WEBSITE_HOSTNAME", "")
        settings.app_base_url = f"https://{_host}" if _host else f"http://localhost:{settings.port}"
    logger.info(f"   Self base URL: {settings.app_base_url}")

    from app.db.cosmos import init_cosmos, close_cosmos
    cosmos_ok = await init_cosmos()
    logger.info(f"   Cosmos DB: {'connected' if cosmos_ok else 'in-memory fallback'}")

    # Hydrate external agent registry from Cosmos (falls back to disk JSON).
    from app.orchestrator.registry import load_registry_from_cosmos
    await load_registry_from_cosmos()
    logger.info("   Agent registry: loaded")

    # Note: demo peers are no longer seeded — peer discovery is organic via
    # Entra ID sign-ups. New users become discoverable as soon as they sign in.

    # Kick off calendar notification scanner (every 30s).
    from app.agents.calendar_agent import start_notification_scanner, stop_notification_scanner, seed_holidays
    start_notification_scanner(interval_seconds=30)

    # Seed Indian holidays for 2026 for a default user (will be seeded per-user on first calendar query).
    logger.info("   Holidays: seeded for default calendar")

    yield

    # Shutdown
    stop_notification_scanner()
    await close_cosmos()
    logger.info("👋 Lumen Demo shut down")


app = FastAPI(
    title="Lumen Demo",
    version="1.0.0",
    description="Persistent personal learning agent with mock Teaching Assistants",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Serve the React SPA index."""
    spa_index = os.path.join(_frontend_dist, "index.html")
    if os.path.exists(spa_index):
        return FileResponse(spa_index, headers={"Cache-Control": "no-store, must-revalidate"})
    from fastapi.responses import HTMLResponse
    return HTMLResponse("<html><body><h2>Lumen is starting up…</h2><p>Refresh in a moment.</p></body></html>", status_code=503)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "type": "lumen-demo"}


# Routes
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.lumen_api import router as lumen_router
from app.routes.lumen_social import router as social_router
from app.orchestrator.registry import router as agents_router
from app.protocols.a2a import router as a2a_router
from app.protocols.lumen_a2a import router as lumen_a2a_router
from app.routes.portfolio import router as portfolio_router
from app.routes.coding_ta import router as coding_ta_router
from app.routes.shiksha import router as shiksha_router
from app.routes.notion import router as notion_router
from app.routes.google import router as google_router

app.include_router(auth_router, prefix="/auth")
app.include_router(chat_router, prefix="/chat")
app.include_router(lumen_router, prefix="/lumen")
app.include_router(social_router, prefix="/lumen")
app.include_router(agents_router, prefix="/agents")
app.include_router(a2a_router)
app.include_router(lumen_a2a_router)
app.include_router(portfolio_router, prefix="/portfolio")
app.include_router(coding_ta_router, prefix="/coding-ta")
app.include_router(shiksha_router, prefix="/shiksha")
# Notion + Google routes carry their own /auth/* and /lumen/* prefixes
app.include_router(notion_router)
app.include_router(google_router)

# ── Lumen v2 (Magentic-One) — additive mount; never alters v1 routes ─────────
# Guarded so a missing/broken v2 (e.g. autogen not installed) can never affect v1.
try:
    from v2.router import router as v2_router
    app.include_router(v2_router, prefix="/v2")
    logger.info("   Lumen v2: mounted at /v2")
except Exception as _v2_err:
    logger.warning(f"   Lumen v2: not mounted ({_v2_err})")


# Calendar agent endpoints
from app.agents.calendar_agent import generate_study_plan, schedule_event, get_user_events, update_event_status, delete_event, parse_and_schedule, get_prefs, set_prefs, get_notifications, mark_notifications_read, start_notification_scanner
from app.middleware.auth import get_current_user
from fastapi import Depends
from pydantic import BaseModel


@app.get("/agents/calendar/plan")
async def calendar_plan(current_user: dict = Depends(get_current_user)):
    return await generate_study_plan(current_user["id"])


class ScheduleEventBody(BaseModel):
    title: str
    event_type: str = "study"
    date: str | None = None
    time: str | None = None
    duration_mins: int = 60
    description: str = ""
    ta_id: str | None = None
    reminder_minutes_before: int | None = None
    notify_at_start: bool | None = None


class NaturalScheduleBody(BaseModel):
    message: str


class PrefsBody(BaseModel):
    reminder_minutes_before: int | None = None
    notify_at_start: bool | None = None


class MarkReadBody(BaseModel):
    ids: list[str] | None = None


@app.post("/agents/calendar/schedule")
async def calendar_schedule(body: ScheduleEventBody, current_user: dict = Depends(get_current_user)):
    """Schedule an event with explicit fields."""
    event = await schedule_event(
        user_id=current_user["id"], title=body.title, event_type=body.event_type,
        date=body.date, time=body.time, duration_mins=body.duration_mins,
        description=body.description, ta_id=body.ta_id,
        reminder_minutes_before=body.reminder_minutes_before,
        notify_at_start=body.notify_at_start,
    )
    return {"ok": True, "event": event}


@app.post("/agents/calendar/schedule-natural")
async def calendar_schedule_natural(body: NaturalScheduleBody, current_user: dict = Depends(get_current_user)):
    """Parse a natural language message into a calendar event."""
    return await parse_and_schedule(current_user["id"], body.message)


@app.get("/agents/calendar/events")
async def calendar_events(current_user: dict = Depends(get_current_user)):
    """Get all scheduled events."""
    from app.agents.calendar_agent import seed_holidays
    seed_holidays(current_user["id"])  # Ensure holidays are present
    events = await get_user_events(current_user["id"])
    return {"events": events, "count": len(events)}


class EventStatusBody(BaseModel):
    status: str  # completed, cancelled


@app.put("/agents/calendar/events/{event_id}")
async def calendar_update_event(event_id: str, body: EventStatusBody, current_user: dict = Depends(get_current_user)):
    """Update event status."""
    event = await update_event_status(current_user["id"], event_id, body.status)
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True, "event": event}


@app.delete("/agents/calendar/events/{event_id}")
async def calendar_delete_event(event_id: str, current_user: dict = Depends(get_current_user)):
    """Delete an event. Holidays are protected and cannot be deleted."""
    from app.agents.calendar_agent import get_user_events
    events = await get_user_events(current_user["id"])
    target = next((e for e in events if e["id"] == event_id), None)
    if target and target.get("type") == "holiday":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=f"'{target.get('title')}' is a holiday and cannot be removed.")
    ok = await delete_event(current_user["id"], event_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True}


# ── Calendar notification preferences & inbox ────────────────

@app.get("/agents/calendar/preferences")
async def calendar_get_prefs(current_user: dict = Depends(get_current_user)):
    return get_prefs(current_user["id"])


@app.post("/agents/calendar/preferences")
async def calendar_set_prefs(body: PrefsBody, current_user: dict = Depends(get_current_user)):
    prefs = set_prefs(current_user["id"], body.model_dump())
    return {"ok": True, "preferences": prefs}


@app.get("/agents/calendar/notifications")
async def calendar_notifications(unread_only: bool = False,
                                 current_user: dict = Depends(get_current_user)):
    notes = get_notifications(current_user["id"], unread_only=unread_only)
    return {"notifications": notes, "unread": sum(1 for n in notes if not n["read"])}


@app.post("/agents/calendar/notifications/read")
async def calendar_mark_read(body: MarkReadBody, current_user: dict = Depends(get_current_user)):
    count = mark_notifications_read(current_user["id"], body.ids)
    return {"ok": True, "marked": count}


# ── Communication Agent endpoints ────────────────────────────
from app.agents.communication_agent import check_inbox, check_outbox, mark_inbox_read, compose_draft
from app.agents.graph_mail import (
    list_inbox as graph_list_inbox,
    get_email as graph_get_email,
    mark_as_read as graph_mark_as_read,
    search_emails as graph_search_emails,
)


@app.get("/lumen/comm/inbox")
async def comm_inbox(from_filter: str | None = None, current_user: dict = Depends(get_current_user)):
    """Get inbox — shows simulated demo messages."""
    msgs = check_inbox(current_user["id"], from_filter)
    return {"messages": msgs, "count": len(msgs)}


@app.get("/lumen/email/inbox")
async def email_inbox(
    graph_token: str | None = None,
    limit: int = 20,
    unread_only: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Get real Outlook inbox via Microsoft Graph API.

    Args:
        graph_token: User's Microsoft Graph OAuth token
        limit: Max emails (1-50)
        unread_only: Only return unread messages

    Returns: List of emails with subject, sender, preview, etc.
    """
    if not graph_token:
        return {"error": "graph_token required for Outlook access", "messages": []}

    emails = await graph_list_inbox(graph_token, filter_unread=unread_only, limit=limit)
    return {"messages": emails, "count": len(emails)}


@app.get("/lumen/comm/outbox")
async def comm_outbox(current_user: dict = Depends(get_current_user)):
    msgs = check_outbox(current_user["id"])
    return {"messages": msgs, "count": len(msgs)}


class CommReadBody(BaseModel):
    ids: list[str]


@app.post("/lumen/comm/read")
async def comm_mark_read(body: CommReadBody, current_user: dict = Depends(get_current_user)):
    count = mark_inbox_read(body.ids)
    return {"ok": True, "marked": count}


class SendRealBody(BaseModel):
    draft: dict
    graph_token: str
    provider: str = ""  # "google" or "microsoft" — auto-detected if empty


@app.post("/lumen/comm/send-real")
async def comm_send_real(body: SendRealBody, current_user: dict = Depends(get_current_user)):
    """Send email — auto-detects provider from user email."""
    from app.agents.communication_agent import send_via_smtp, send_via_graph
    from app.agents.interaction_manager import clear_pending_draft
    draft = dict(body.draft or {})
    draft["user_id"] = current_user["id"]
    user_email = current_user.get("email", "")
    provider = body.provider or ""

    is_google = current_user.get("id", "").startswith("google-") or provider == "google"

    if is_google:
        # Use the connected Google OAuth token (auto-refreshes) and the new gmail_agent.
        from app.agents.gmail_agent import get_valid_google_token, send_gmail
        from app.agents.communication_agent import _comm_outbox, _now
        token = await get_valid_google_token(current_user["id"])
        if not token:
            result = {"status": "failed", "error": "Google not connected. Open Profile → Connect Google."}
        else:
            sent = await send_gmail(
                token,
                to=draft.get("to_email") or draft.get("to", ""),
                subject=draft.get("subject", ""),
                body=draft.get("body", ""),
                sender_email=user_email,
            )
            if sent.get("status") == "sent":
                # Log into the comms outbox so "what did I send today?" still works.
                _comm_outbox.append({
                    **draft,
                    "status": "sent",
                    "sent_at": _now(),
                    "method": "gmail-api",
                    "to_email": sent.get("to") or draft.get("to_email"),
                })
            result = sent
    else:
        # Microsoft: try SMTP then Graph
        result = await send_via_smtp(user_email, body.graph_token, draft, provider="microsoft")
        if result.get("status") != "sent":
            result = await send_via_graph(body.graph_token, draft)

    clear_pending_draft(current_user["id"])
    return result


class SendWorkIQBody(BaseModel):
    draft: dict


@app.post("/lumen/comm/send-workiq")
async def comm_send_workiq(
    body: SendWorkIQBody,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Send email via WorkIQ Mail MCP using the user's Entra token.

    No MSAL popup or admin consent required — the token is forwarded
    directly to the WorkIQ MCP server (OAuth Identity Passthrough).
    Falls back to a mailto: link if WorkIQ is unavailable.
    """
    from app.tools.workiq_mail import send_email as workiq_send
    from app.agents.interaction_manager import clear_pending_draft

    # Extract the raw Entra token from the Authorization header
    auth_header = request.headers.get("Authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip()

    draft = dict(body.draft or {})
    to_email = draft.get("to_email", "")
    subject = draft.get("subject", "Message from Lumen")
    email_body = draft.get("body", "")

    if not to_email:
        return {"status": "failed", "error": "Recipient email is required."}

    result = await workiq_send(user_token, to_email, subject, email_body)

    if result.get("status") == "sent":
        clear_pending_draft(current_user["id"])

    # Always include mailto fallback URL
    import urllib.parse
    result["mailto_url"] = (
        f"mailto:{urllib.parse.quote(to_email)}"
        f"?subject={urllib.parse.quote(subject)}"
        f"&body={urllib.parse.quote(email_body)}"
    )
    return result


# ── Real Outlook Email endpoints ─────────────────────────────

@app.get("/lumen/email/{message_id}")
async def email_get(message_id: str, graph_token: str | None = None, current_user: dict = Depends(get_current_user)):
    """Get full email content by ID."""
    if not graph_token:
        return {"error": "graph_token required"}
    email = await graph_get_email(graph_token, message_id)
    return email or {"error": f"Email {message_id} not found"}


class EmailMarkReadBody(BaseModel):
    message_ids: list[str]


@app.post("/lumen/email/mark-read")
async def email_mark_read(body: EmailMarkReadBody, graph_token: str | None = None, current_user: dict = Depends(get_current_user)):
    """Mark emails as read in Outlook."""
    if not graph_token:
        return {"error": "graph_token required"}
    count = await graph_mark_as_read(graph_token, body.message_ids)
    return {"ok": True, "marked": count}


@app.get("/lumen/email/search")
async def email_search(query: str, graph_token: str | None = None, limit: int = 20, current_user: dict = Depends(get_current_user)):
    """Search Outlook emails by subject, body, or sender."""
    if not graph_token:
        return {"error": "graph_token required", "results": []}
    if not query:
        return {"error": "query parameter required", "results": []}
    results = await graph_search_emails(graph_token, query, limit)
    return {"results": results, "count": len(results)}


class DraftComposeBody(BaseModel):
    message: str


@app.post("/lumen/email/compose")
async def email_compose(body: DraftComposeBody, current_user: dict = Depends(get_current_user)):
    """Compose an email draft from natural language using LLM.

    Returns: Draft object with to, subject, body, etc.
    """
    _block_entra_email(current_user)
    draft = await compose_draft(
        user_id=current_user["id"],
        user_name=current_user.get("name", ""),
        message=body.message,
        user_email=current_user.get("email", ""),
    )
    return draft


# ── Chrome Extension endpoints (DOM-based Outlook integration) ──────────
# These power the "Lumen for Outlook" Chrome extension. The extension reads
# email from the user's authenticated Outlook Web DOM and asks Lumen to
# generate replies / compose drafts. The user's Outlook session sends the
# email — Lumen never touches the mailbox.

class ExtensionReplyBody(BaseModel):
    subject: str = ""
    sender: str = ""
    sender_email: str = ""
    body: str
    instruction: str


class ExtensionComposeBody(BaseModel):
    to: str = ""
    subject: str = ""
    instruction: str


class ExtensionLogSentBody(BaseModel):
    to: str
    subject: str = ""
    body: str = ""


async def _ext_llm_call(system: str, user: str) -> str:
    """Run a one-shot LLM call using Lumen's existing Azure OpenAI client."""
    from app.agents.calendar_agent import _get_client
    client = _get_client()
    agent = client.as_agent(name="EmailAssistant", instructions=system)
    result = await agent.run(user)
    text = str(result).strip()
    # Strip markdown code fences in case the model wrapped its output
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text


@app.post("/lumen/comm/extension/reply")
async def ext_reply(body: ExtensionReplyBody, current_user: dict = Depends(get_current_user)):
    """Generate an AI reply to an email. Called by the Chrome extension."""
    user_name = current_user.get("name", "")
    from app.agents.prompt_kit import build_agent_prompt
    system = build_agent_prompt(
        role="Outlook Reply Assistant",
        mission="Draft a reply to the email the user is reading in Outlook, following their instruction.",
        capabilities=[
            "Write a professional, context-aware reply to an incoming email.",
            "Match whatever tone the user asks for (formal, friendly, brief, …).",
        ],
        rules=[
            "Return ONLY the email body — no subject line, no markdown, no surrounding quotes.",
            "Follow the user's instruction for tone and content.",
            f"End with a short sign-off using the sender's name: {user_name or 'the user'}.",
            "Stay grounded in the original email — don't invent facts.",
        ],
        output_format="Plain text — just the reply body.",
    )
    user_prompt = (
        f"ORIGINAL EMAIL:\n"
        f"From: {body.sender} <{body.sender_email}>\n"
        f"Subject: {body.subject}\n\n"
        f"{body.body[:6000]}\n\n"
        f"---\n"
        f"USER INSTRUCTION: {body.instruction}\n\n"
        f"Write the reply body now."
    )
    try:
        reply = await _ext_llm_call(system, user_prompt)
        return {"reply": reply}
    except Exception as e:
        logger.warning(f"ext_reply LLM error: {e}")
        return {"reply": "", "error": str(e)}


@app.post("/lumen/comm/extension/compose")
async def ext_compose(body: ExtensionComposeBody, current_user: dict = Depends(get_current_user)):
    """Generate a fresh email body from a natural-language instruction. Called by extension."""
    user_name = current_user.get("name", "")
    from app.agents.prompt_kit import build_agent_prompt
    system = build_agent_prompt(
        role="Outlook Compose Assistant",
        mission="Compose a fresh email body from the user's natural-language instruction, to be sent from their Outlook.",
        capabilities=[
            "Write a clear, professional email from a short instruction.",
            "Use the recipient and subject as context when provided.",
        ],
        rules=[
            "Return ONLY the email body — no subject line, no markdown, no surrounding quotes.",
            "Keep it concise and clear.",
            f"End with a short sign-off using the sender's name: {user_name or 'the user'}.",
            "Only include what the instruction asks for — don't pad with invented detail.",
        ],
        output_format="Plain text — just the email body.",
    )
    parts = []
    if body.to: parts.append(f"Recipient: {body.to}")
    if body.subject: parts.append(f"Subject: {body.subject}")
    parts.append(f"What the email should say: {body.instruction}")
    user_prompt = "\n".join(parts) + "\n\nWrite the email body now."
    try:
        email_body = await _ext_llm_call(system, user_prompt)
        return {"email_body": email_body}
    except Exception as e:
        logger.warning(f"ext_compose LLM error: {e}")
        return {"email_body": "", "error": str(e)}


@app.post("/lumen/comm/extension/log-sent")
async def ext_log_sent(body: ExtensionLogSentBody, current_user: dict = Depends(get_current_user)):
    """Record that the user sent an email via the extension (Outlook does the send)."""
    from app.agents.communication_agent import log_extension_sent
    entry = log_extension_sent(
        user_id=current_user["id"],
        to_email=body.to,
        subject=body.subject,
        body=body.body,
    )
    return {"logged": True, "id": entry["id"]}


# ── IMAP — REMOVED ──────────────────────────────────────────────────────
# IMAP/SMTP support was removed. Lumen now uses Gmail API for Google users
# and the Chrome extension for Outlook users. A connection-status endpoint
# is kept so existing frontend code paths get a clean "not connected" reply
# instead of a 404. /lumen/email/imap/* endpoints are intentionally gone.

@app.get("/lumen/email/connection-status")
async def email_connection_status(current_user: dict = Depends(get_current_user)):
    """Always returns 'not connected' now that IMAP is removed.

    Kept so the frontend's existing check doesn't 404 — it just sees False
    and routes sends through Gmail API (Google users) or the extension.
    """
    return {"connected": False, "email": "", "imap_host": "", "blocked": False}


# Events bus endpoint
from app.events.bus import get_recent_events


@app.get("/.well-known/agent-card.json")
async def lumen_system_card(request: Request):
    """Lumen system orchestrator card — public, no auth required. A2A v1.0.0."""
    base = str(request.base_url).rstrip("/")
    return {
        "name": "Lumen",
        "description": (
            "Personal AI learning companion and multi-agent orchestrator. "
            "Each student has their own Lumen in the Lumen Network — it tracks learning progress, "
            "routes to specialist Teaching Assistants, manages schedule and communications, "
            "and connects with peers via A2A."
        ),
        "version": "1.0.0",
        "documentationUrl": f"{base}/docs",
        "iconUrl": f"{base}/icon.png",
        "provider": {"organization": "Lumen Network", "url": base},
        "supportedInterfaces": [
            {"url": f"{base}/a2a/lumen", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": True,
            "extensions": [
                {
                    "uri": f"{base}/extensions/litp/v1",
                    "description": "Learning Interaction Tracking Protocol — threshold concept inventory and curriculum progression",
                    "required": False,
                }
            ],
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "securitySchemes": {
            "entraId": {
                "openIdConnectSecurityScheme": {
                    "openIdConnectUrl": "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/.well-known/openid-configuration",
                    "description": "Microsoft Entra ID — for microsoft.com users",
                }
            },
            "lumenJwt": {
                "httpAuthSecurityScheme": {
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Lumen-issued JWT after successful Entra/Google login",
                }
            },
        },
        "securityRequirements": [{"entraId": []}, {"lumenJwt": []}],
        "skills": [
            {"id": "lumen.progress-query", "name": "Learning Progress Query",
             "description": "Query curriculum progress, threshold concept mastery, session history across all TAs",
             "tags": ["learning", "progress", "curriculum", "threshold-concepts", "education"],
             "examples": ["What have I covered so far?", "What should I study next?", "Show my TC mastery", "How far am I in my course?"]},
            {"id": "lumen.ta-routing", "name": "Teaching Assistant Routing",
             "description": "Route learning queries to the appropriate specialist TA based on content",
             "tags": ["routing", "education", "teaching", "shiksha"],
             "examples": ["Help me understand blockchain", "Open my Shiksha TA", "I want to learn something new"]},
            {"id": "lumen.peer-network", "name": "Lumen Network Peer Messaging",
             "description": "Discover other Lumen users, compare progress, send A2A messages to peers",
             "tags": ["social", "peer", "network", "messaging", "a2a"],
             "examples": ["Who else is learning at my level?", "Message Priya about the study session", "Find peers studying blockchain"]},
            {"id": "lumen.scheduling", "name": "Study Scheduling",
             "description": "Generate personalized study schedules based on progress gaps",
             "tags": ["scheduling", "study-plan", "calendar", "planning"],
             "examples": ["Create a weekly study plan", "Schedule sessions for my weak areas"]},
            {"id": "lumen.communications", "name": "Email & Communications",
             "description": "Draft and send emails via SMTP or Outlook",
             "tags": ["email", "outlook", "communication", "smtp"],
             "examples": ["Email my professor", "Draft a message to the team"]},
            {"id": "lumen.portfolio", "name": "GitHub Agent",
             "description": "Explore repos (commits, merges, rebases, branches, PRs, files), review code, manage files/repos with approval, run the learning-portfolio artifact flow, and inspect GitHub Classroom",
             "tags": ["github", "portfolio", "artifacts", "projects", "commits", "classroom"],
             "examples": ["Show my portfolio", "Save this solution", "List my recent commits", "Any rebases on main?", "Open the GitHub agent"]},
            {"id": "lumen.shiksha", "name": "Shiksha Course Bridge",
             "description": "Discover and interact with Shiksha/Ekalaiva TAs",
             "tags": ["shiksha", "ekalaiva", "courses", "teaching"],
             "examples": ["What Shiksha courses am I in?", "Show my blockchain progress"]},
        ],
    }


@app.get("/.well-known/agent.json")
async def well_known_agent_json_redirect():
    """Backwards compat redirect to correct A2A v1.0.0 path."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/.well-known/agent-card.json", status_code=301)


@app.get("/extensions/litp/v1")
async def litp_spec(request: Request):
    """LITP — Learning Interaction Tracking Protocol spec (A2A extension).
    Describes how Lumen tracks threshold concepts and curriculum progression.
    No auth required — public spec endpoint."""
    base = str(request.base_url).rstrip("/")
    return {
        "uri": f"{base}/extensions/litp/v1",
        "name": "Learning Interaction Tracking Protocol",
        "version": "1.0",
        "description": (
            "LITP defines how Lumen tracks threshold concept mastery and "
            "curriculum progression across Teaching Assistants. "
            "An agent that speaks LITP can query and update a student's TC inventory."
        ),
        "schemas": {
            "TCInventory": {
                "type": "object",
                "description": "Threshold Concept inventory for a student",
                "properties": {
                    "mastered": {"type": "array", "items": {"type": "string"}, "description": "TC IDs fully mastered"},
                    "in_progress": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tc_id": {"type": "string"},
                                "progress_pct": {"type": "number", "minimum": 0, "maximum": 100},
                                "sessions": {"type": "integer"},
                            }
                        }
                    },
                    "not_started": {"type": "array", "items": {"type": "string"}},
                },
            },
            "CurriculumProgress": {
                "type": "object",
                "description": "Per-TA curriculum progression record",
                "properties": {
                    "ta_id": {"type": "string"},
                    "current_level": {"type": "integer"},
                    "current_module": {"type": "string"},
                    "session_count": {"type": "integer"},
                    "mastered_concepts": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "endpoints": {
            "get_progress": {
                "method": "GET",
                "path": f"{base}/lumen/progress",
                "auth": "lumenJwt",
                "description": "Get the calling student's full TC inventory and curriculum progress",
            },
            "update_progress": {
                "method": "POST",
                "path": f"{base}/lumen/progress",
                "auth": "lumenJwt",
                "description": "TA posts a progress update after a learning session",
                "body": {"ta_id": "string", "concepts_covered": ["string"], "tc_updates": "object"},
            },
        },
    }


@app.get("/agents/directory")
async def public_agent_directory(request: Request):
    """Public directory of all agents and discoverable Lumen instances. No auth required."""
    base = str(request.base_url).rstrip("/")
    from app.orchestrator.registry import AGENT_ROUTES, _external_agents, get_agent_card
    from app.lumen.core import get_all_lumens

    agents = []
    for agent_id in AGENT_ROUTES:
        card = get_agent_card(agent_id, base)
        agents.append({
            "type": "agent",
            "id": agent_id,
            "name": card.name if card else agent_id,
            "description": card.description if card else "",
            "card_url": f"{base}/agents/{agent_id}/agent-card.json",
            "subjects": [s.id for s in card.skills] if card else [],
        })

    for agent_id, agent in _external_agents.items():
        agents.append({
            "type": "external-agent",
            "id": agent_id,
            "name": agent.get("name", agent_id),
            "description": agent.get("description", ""),
            "card_url": agent.get("card_url", ""),
            "endpoint": agent.get("endpoint", ""),
        })

    all_lumens = await get_all_lumens()
    lumen_entries = []
    for lumen in all_lumens:
        if not lumen.get("social", {}).get("discoverable", False):
            continue
        progress = lumen.get("curriculum_progress", {})
        lumen_entries.append({
            "type": "lumen",
            "id": lumen["id"],
            "name": lumen.get("name", "Student"),
            "card_url": f"{base}/a2a/lumen/{lumen['id']}/agent-card.json",
            "subjects": list(progress.keys()),
        })

    return {
        "system_card": f"{base}/.well-known/agent-card.json",
        "total": len(agents) + len(lumen_entries),
        "agents": agents,
        "lumens": lumen_entries,
    }


# Events bus endpoint
from app.events.bus import get_recent_events


@app.get("/events")
async def events(event_type: str | None = None, limit: int = 20):
    return get_recent_events(event_type, limit)


# ── Azure Speech token endpoint ──────────────────────────────

@app.get("/lumen/speech-token")
async def speech_token(current_user: dict = Depends(get_current_user)):
    """Return a short-lived Azure Speech token + region for the frontend SDK.
    Frontend uses this to init SpeechConfig.fromAuthorizationToken(token, region).
    If no speech key is configured, returns empty so frontend falls back to Web Speech API."""
    if not settings.azure_speech_key:
        return {"token": "", "region": settings.azure_speech_region, "available": False}
    try:
        import httpx
        url = f"https://{settings.azure_speech_region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key})
            resp.raise_for_status()
            return {"token": resp.text, "region": settings.azure_speech_region, "available": True}
    except Exception as e:
        logger.warning(f"Speech token fetch failed: {e}")
        return {"token": "", "region": settings.azure_speech_region, "available": False}


# ── Widget endpoints ─────────────────────────────────────────

from app.lumen.widget_manager import get_widgets, add_widget, remove_widget_by_id, WIDGET_TEMPLATES


@app.get("/lumen/widgets")
async def widgets_get(current_user: dict = Depends(get_current_user)):
    """Get user's dashboard widgets."""
    return {"widgets": get_widgets(current_user["id"])}


class WidgetAddBody(BaseModel):
    template: str


@app.post("/lumen/widgets")
async def widgets_add(body: WidgetAddBody, current_user: dict = Depends(get_current_user)):
    """Add a widget to the dashboard."""
    w = add_widget(current_user["id"], body.template)
    if not w:
        return {"ok": False, "error": "Unknown template or already added"}
    return {"ok": True, "widget": w, "widgets": get_widgets(current_user["id"])}


@app.delete("/lumen/widgets/{widget_id}")
async def widgets_remove(widget_id: str, current_user: dict = Depends(get_current_user)):
    """Remove a widget from the dashboard."""
    ok = remove_widget_by_id(current_user["id"], widget_id)
    return {"ok": ok, "widgets": get_widgets(current_user["id"])}


@app.get("/lumen/widget-templates")
async def widget_templates():
    """List available widget templates."""
    return {"templates": [
        {"key": k, "title": v["title"]} for k, v in WIDGET_TEMPLATES.items()
    ]}


# ── MCP Tools status endpoint ───────────────────────────────

@app.get("/lumen/tools")
async def tools_status():
    """List all MCP tools and their configuration status."""
    from app.tools.mcp_registry import get_configured_tools
    return {"tools": get_configured_tools()}


# ── A2UI Action endpoint ─────────────────────────────────────

class A2UIActionBody(BaseModel):
    action: str
    data: dict = {}
    thread_id: str = ""


@app.post("/a2ui/action")
async def a2ui_action(body: A2UIActionBody, current_user: dict = Depends(get_current_user)):
    """Handle A2UI button clicks and form submissions.
    Routes the action back through dispatch as a structured command."""
    from app.agents.interaction_manager import dispatch
    # Construct a message from the action
    message = body.action
    if body.data:
        # For form submissions, include form data
        import json as _j
        message = f"[A2UI Action: {body.action}] {_j.dumps(body.data)}"

    result = await dispatch(
        user_id=current_user["id"],
        message=message,
        thread_id=body.thread_id or None,
        user_info=current_user,
    )
    return result


# ── UX Agent endpoints ──────────────────────────────────────

from app.lumen.ux_agent import get_ux_preset, set_ux_preset, get_all_presets, detect_preset_switch


@app.get("/lumen/ux")
async def ux_get(current_user: dict = Depends(get_current_user)):
    """Get current UX preset and list of all available presets."""
    preset = await get_ux_preset(current_user["id"])
    return {"active": preset, "presets": get_all_presets()}


class UxSetBody(BaseModel):
    preset_id: str


@app.put("/lumen/ux")
async def ux_set(body: UxSetBody, current_user: dict = Depends(get_current_user)):
    """Switch UX preset."""
    try:
        preset = await set_ux_preset(current_user["id"], body.preset_id)
        return {"ok": True, "active": preset}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


# ── AG-UI Streaming endpoint ────────────────────────────────

from fastapi.responses import StreamingResponse
import json as _json


@app.post("/ag-ui/chat")
async def agui_chat_stream(request: Request, current_user: dict = Depends(get_current_user)):
    """AG-UI compatible SSE streaming endpoint.
    Wraps existing dispatch and streams response events."""
    body = await request.json()
    message = body.get("message", "")
    thread_id = body.get("thread_id")
    graph_token = body.get("graph_token")

    from app.agents.interaction_manager import dispatch
    from app.routes.chat import create_thread, append_message
    from app.db.cosmos import get_thread

    if not thread_id:
        thread = await create_thread(current_user["id"], title=message[:50], channel="lumen")
        thread_id = thread["id"]

    # Load conversation history for context
    thread_data = await get_thread(current_user["id"], thread_id)
    conversation_history = []
    if thread_data and thread_data.get("messages"):
        for m in thread_data["messages"][-20:]:
            role = m.get("role", "user")
            if role == "assistant": role = "assistant"
            conversation_history.append({"role": role, "content": m.get("content", "")})

    async def event_stream():
        # RunStarted
        yield f"data: {_json.dumps({'type': 'RUN_STARTED', 'threadId': thread_id})}\n\n"

        # Get response from existing dispatch with conversation history
        result = await dispatch(
            user_id=current_user["id"],
            message=message,
            thread_id=thread_id,
            user_info=current_user,
            conversation_history=conversation_history or None,
            graph_token=graph_token,
        )

        await append_message(current_user["id"], thread_id, "user", message)
        await append_message(current_user["id"], thread_id, "assistant", result.get("reply", ""))

        reply = result.get("reply", "")

        # TextMessageStart
        yield f"data: {_json.dumps({'type': 'TEXT_MESSAGE_START', 'messageId': thread_id + '-resp', 'role': 'assistant'})}\n\n"

        # Stream text in chunks for progressive rendering
        chunk_size = 8
        words = reply.split(" ")
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            if i > 0:
                chunk = " " + chunk
            yield f"data: {_json.dumps({'type': 'TEXT_MESSAGE_CONTENT', 'messageId': thread_id + '-resp', 'delta': chunk})}\n\n"

        # TextMessageEnd
        yield f"data: {_json.dumps({'type': 'TEXT_MESSAGE_END', 'messageId': thread_id + '-resp'})}\n\n"

        # Emit A2UI CUSTOM event — prefer native a2ui from dispatch, fall back to card conversion
        # Only emit A2UI doc if the agent explicitly produced one — otherwise we get
        # duplicate cards (React renders both the cards array AND the A2UI surface).
        a2ui_doc = result.get("a2ui")
        if a2ui_doc:
            yield f"data: {_json.dumps({'type': 'CUSTOM', 'name': 'a2ui', 'value': a2ui_doc})}\n\n"

        # StateSnapshot with structured metadata
        meta = {
            "action": result.get("action"),
            "cards": result.get("cards", []),
            "redirect_url": result.get("redirect_url"),
            "agent_id": result.get("agent_id"),
            "intent": result.get("intent"),
            "proposal": result.get("proposal"),
            "thread_id": thread_id,
        }
        yield f"data: {_json.dumps({'type': 'STATE_SNAPSHOT', 'snapshot': meta})}\n\n"

        # RunFinished
        yield f"data: {_json.dumps({'type': 'RUN_FINISHED', 'threadId': thread_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _cards_to_a2ui(cards: list, thread_id: str) -> dict | None:
    """Convert Lumen cards to A2UI document format."""
    if not cards:
        return None
    components = []
    children_ids = []
    for i, card in enumerate(cards):
        ctype = card.get("type", "")
        data = card.get("data", {})
        cid = f"{thread_id}-c{i}"
        children_ids.append(cid)

        if ctype == "progress":
            components.extend([
                {"id": cid, "type": "Card", "props": {"variant": "outlined"}, "children": [f"{cid}-h", f"{cid}-bar", f"{cid}-stats"]},
                {"id": f"{cid}-h", "type": "Heading", "props": {"text": data.get("ta_name", "Progress"), "level": 3}},
                {"id": f"{cid}-bar", "type": "ProgressBar", "props": {"label": f"Level {data.get('level', 1)} — {data.get('label', '')}", "value": data.get("pct", 0)}},
                {"id": f"{cid}-stats", "type": "Row", "props": {"label": f"{data.get('sessions', 0)} sessions", "value": f"{len(data.get('topics_mastered', []))} mastered"}},
            ])
        elif ctype == "events":
            rows = [[e.get("title", ""), e.get("date", ""), e.get("time", ""), e.get("type", "")] for e in (data if isinstance(data, list) else [])]
            components.append({"id": cid, "type": "Table", "props": {"columns": ["Event", "Date", "Time", "Type"], "rows": rows}})
        elif ctype == "email_draft":
            components.extend([
                {"id": cid, "type": "Card", "props": {"variant": "elevated"}, "children": [f"{cid}-h", f"{cid}-to", f"{cid}-subj", f"{cid}-body"]},
                {"id": f"{cid}-h", "type": "Heading", "props": {"text": "Email Draft", "level": 3}},
                {"id": f"{cid}-to", "type": "KeyValue", "props": {"label": "To", "value": f"{data.get('to', '')} <{data.get('to_email', '')}>"}},
                {"id": f"{cid}-subj", "type": "KeyValue", "props": {"label": "Subject", "value": data.get("subject", "")}},
                {"id": f"{cid}-body", "type": "Text", "props": {"text": data.get("body", "")}},
            ])
        elif ctype == "ux_preset":
            components.extend([
                {"id": cid, "type": "Alert", "props": {"title": f"{data.get('icon', '✦')} {data.get('name', 'Preset')}", "message": data.get("description", ""), "tone": "success"}},
            ])
        else:
            # Generic card
            components.append({"id": cid, "type": "Card", "props": {"variant": "outlined"}, "children": []})

    if not components:
        return None

    root_id = f"{thread_id}-root"
    components.insert(0, {"id": root_id, "type": "List", "props": {}, "children": children_ids})
    return {"surface": "chat", "root": root_id, "components": components}


# SPA pages
import os
from fastapi.responses import FileResponse

_public = os.path.join(os.path.dirname(__file__), "..", "public")

# Resolve frontend/dist — try several candidate paths so it works both
# locally (relative to __file__) and on Azure (relative to CWD / known mount).
def _find_frontend_dist() -> str:
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"),
        os.path.join(os.getcwd(), "frontend", "dist"),
        "/home/site/wwwroot/frontend/dist",
        # Fallback: some deploy zips land dist/ at wwwroot root
        os.path.join(os.getcwd(), "dist"),
        "/home/site/wwwroot/dist",
    ]
    for p in candidates:
        if os.path.isfile(os.path.join(p, "index.html")):
            return os.path.abspath(p)
    return os.path.abspath(candidates[0])  # fall back to first; root() will return 503

_frontend_dist = _find_frontend_dist()


@app.get("/legacy-login")
@app.get("/legacy")
@app.get("/demo")
async def legacy_redirect():
    """Permanently redirect all legacy entry points to the React SPA."""
    return RedirectResponse(url="/", status_code=301)


@app.get("/ta/math")
async def math_ta_page():
    return FileResponse(os.path.join(_public, "ta-math.html"))


@app.get("/ta/cs")
async def cs_ta_page():
    return FileResponse(os.path.join(_public, "ta-cs.html"))


@app.get("/ta/calendar")
async def calendar_page():
    return FileResponse(os.path.join(_public, "ta-calendar.html"))


# Static files last
# 1) React build: assets under /assets/* and SPA fallback routes for client-side pages.
if os.path.exists(_frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets")), name="spa-assets")

    @app.get("/dashboard")
    @app.get("/peers")
    @app.get("/privacy")
    @app.get("/portfolio")
    @app.get("/github")
    @app.get("/coding-ta")
    @app.get("/ta")
    @app.get("/login")
    @app.get("/agents")
    @app.get("/calendar")
    @app.get("/v2")
    @app.get("/outlook-signin")
    @app.get("/auth/external-outlook/callback")
    @app.get("/auth/entra-callback")
    @app.get("/auth/notion-callback")
    @app.get("/auth/google-callback")
    @app.get("/auth/github-callback")
    @app.get("/course/{path:path}")
    async def spa_fallback(path: str = ""):
        return FileResponse(os.path.join(_frontend_dist, "index.html"),
                            headers={"Cache-Control": "no-store, must-revalidate"})

# 2) CSS and JS for TA pages (/ta/math, /ta/cs, /ta/calendar)
if os.path.exists(_public):
    _css_dir = os.path.join(_public, "css")
    _js_dir = os.path.join(_public, "js")
    if os.path.isdir(_css_dir):
        app.mount("/css", StaticFiles(directory=_css_dir), name="legacy-css")
    if os.path.isdir(_js_dir):
        app.mount("/js", StaticFiles(directory=_js_dir), name="legacy-js")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
