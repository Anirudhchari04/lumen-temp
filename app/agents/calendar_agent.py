"""Calendar Agent — Study plan generation, scheduling, and event management.

Analyzes student progress gaps, session history, and threshold concepts
to produce personalized study plans. Also manages user-created calendar events
(reminders, deadlines, study sessions). Designed as a micro-agent with its own
A2A-compatible interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import re
import uuid
from datetime import datetime, timedelta, timezone as _tz
UTC = _tz.utc
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
try:
    from agent_framework import Agent
except ImportError:
    from agent_framework._agents import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from app.config import settings
from app.lumen.core import get_lumen
from app.orchestrator.registry import get_all_agents

logger = logging.getLogger(__name__)

_credential = None


def _resolve_events_path() -> Path:
    base = getattr(settings, "lumen_store_path", "") or ""
    if base:
        # Place next to the lumens store.
        return Path(base).parent / "calendar_events.json"
    if os.path.isdir("/home"):
        return Path("/home/data/calendar_events.json")
    return Path("data/calendar_events.json")


_EVENTS_PATH = _resolve_events_path()


def _load_events_store() -> dict[str, dict]:
    try:
        if _EVENTS_PATH.exists():
            with _EVENTS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "events": data.get("events", {}),
                    "prefs": data.get("prefs", {}),
                    "notifications": data.get("notifications", {}),
                }
    except Exception as e:
        logger.warning(f"Failed to load calendar store at {_EVENTS_PATH}: {e}")
    return {"events": {}, "prefs": {}, "notifications": {}}


def _flush_events_store() -> None:
    try:
        _EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _EVENTS_PATH.with_suffix(_EVENTS_PATH.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({
                "events": _user_events,
                "prefs": _user_prefs,
                "notifications": _notifications,
            }, f, ensure_ascii=False, indent=2)
        tmp.replace(_EVENTS_PATH)
    except Exception as e:
        logger.warning(f"Failed to persist calendar store at {_EVENTS_PATH}: {e}")


_store = _load_events_store()
# Event store (keyed by user_id → list[event])
_user_events: dict[str, list[dict]] = _store["events"]
# Notification preferences (keyed by user_id → dict)
_user_prefs: dict[str, dict] = _store["prefs"]
# Unread notifications (keyed by user_id → list)
_notifications: dict[str, list[dict]] = _store["notifications"]

DEFAULT_PREFS = {
    "reminder_minutes_before": 15,
    "notify_at_start": True,
}


def get_prefs(user_id: str) -> dict:
    return {**DEFAULT_PREFS, **_user_prefs.get(user_id, {})}


def set_prefs(user_id: str, patch: dict) -> dict:
    current = get_prefs(user_id)
    current.update({k: v for k, v in patch.items() if v is not None})
    _user_prefs[user_id] = current
    _flush_events_store()
    return current


def _get_credential():
    global _credential
    if _credential is None:
        if settings.azure_managed_identity_client_id:
            _credential = ManagedIdentityCredential(client_id=settings.azure_managed_identity_client_id)
        else:
            _credential = DefaultAzureCredential()
    return _credential


def _get_client():
    return OpenAIChatCompletionClient(
        model=settings.azure_openai_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        credential=_get_credential(),
        api_version=settings.azure_openai_api_version,
    )


from app.agents.prompt_kit import build_agent_prompt

CALENDAR_PROMPT = build_agent_prompt(
    role="Calendar Agent (study planner)",
    mission="Analyze the student's progress across all TAs and generate a concrete, actionable study plan.",
    capabilities=[
        "Read cross-TA progress and threshold-concept (TC) state.",
        "Produce an ordered study plan with specific topics, grouped into sessions.",
        "Sequence prerequisites before the topics that depend on them.",
    ],
    rules=[
        "Output a structured study plan with specific topics and a recommended order.",
        "Prioritize: prerequisites first, then in-progress topics, then new topics.",
        "Consider cross-TA connections (e.g., 'variables' in math helps 'variables' in CS).",
        "Keep plans practical — suggest 2-3 topics per session, max 5 sessions in a plan.",
        "Reference threshold concepts (TCs) — prioritize TCs that are in-progress.",
        "Do NOT teach. Only plan and schedule.",
        "Do NOT use markdown bold (**) or hashtags (##). Use plain text.",
        "Be concise and actionable.",
    ],
    output_format="Plain text — an ordered study plan grouped into sessions, no markdown.",
)


# ── Holiday seeds ────────────────────────────────────────────

_HOLIDAYS_2026 = [
    ("New Year's Day", "2026-01-01", "fixed"),
    ("Republic Day", "2026-01-26", "fixed"),
    ("Holi", "2026-03-04", "fixed"),
    ("Good Friday", "2026-04-03", "optional"),
    ("May Day (Labour Day)", "2026-05-01", "fixed"),
    ("Bakri-Id", "2026-05-28", "fixed"),
    ("Id-E-Milad / Onam", "2026-08-26", "optional"),
    ("Raksha Bandhan", "2026-08-28", "optional"),
    ("Gandhi Jayanti", "2026-10-02", "fixed"),
    ("Bali Padyami", "2026-11-10", "optional"),
    ("Guru Nanak Jayanti", "2026-11-24", "optional"),
    ("Christmas", "2026-12-25", "fixed"),
]


def seed_holidays(user_id: str) -> int:
    """Seed Indian holidays for 2026 into a user's calendar (idempotent)."""
    existing = _user_events.get(user_id, [])
    existing_titles = {e["title"] for e in existing}
    added = 0
    for title, date, kind in _HOLIDAYS_2026:
        if title in existing_titles:
            continue
        event = {
            "id": str(uuid.uuid4())[:8],
            "user_id": user_id,
            "title": title,
            "type": "holiday",
            "date": date,
            "time": "00:00",
            "duration_mins": 1440,
            "description": f"{'Fixed' if kind == 'fixed' else 'Optional'} holiday",
            "ta_id": None,
            "status": "scheduled",
            "reminder_minutes_before": 1440,  # 1 day before
            "notify_at_start": False,
            "_notified_before": False,
            "_notified_start": False,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _user_events.setdefault(user_id, []).append(event)
        added += 1
    if added:
        _flush_events_store()
    return added


async def generate_study_plan(user_id: str) -> dict:
    """Generate a personalized study plan based on current progress."""
    lumen = await get_lumen(user_id)
    if not lumen:
        return {"plan": "No progress data yet. Start by visiting a Teaching Assistant!", "sessions": []}

    progress = lumen.get("curriculum_progress", {})
    tc_inv = lumen.get("tc_inventory", {"mastered": [], "in_progress": []})
    history = lumen.get("session_history", [])[-10:]
    agents = get_all_agents()

    context = {
        "progress": progress,
        "threshold_concepts": {
            "mastered": [t["tc_id"] for t in tc_inv.get("mastered", [])],
            "in_progress": [
                {"tc_id": t["tc_id"], "pct": t.get("progress_pct", 0)}
                for t in tc_inv.get("in_progress", [])
            ],
        },
        "recent_sessions": [
            {"ta": s.get("ta_name", s.get("ta_id", "")), "summary": s.get("summary", "")}
            for s in history[-5:]
        ],
        "available_tas": [{"id": a["id"], "name": a["name"], "subject": a.get("subject", "")} for a in agents if a["id"] != "calendar"],
    }

    prompt = f"{CALENDAR_PROMPT}\n\nStudent data:\n{json.dumps(context, indent=2)}"

    client = _get_client()
    agent = client.as_agent(name="CalendarAgent", instructions=prompt)

    try:
        _t0 = time.perf_counter()
        result = await agent.run("Generate a study plan for this student. Include specific sessions with topics and which TA to use.")
        _latency_ms = (time.perf_counter() - _t0) * 1000
        reply = str(result).replace("**", "").replace("##", "").replace("# ", "")
        try:
            from app.lumen.token_tracker import record_usage, estimate_tokens
            p = estimate_tokens(prompt)
            c = estimate_tokens(reply)
            await record_usage(user_id, p, c, model="agent_framework (estimated)", source="calendar", latency_ms=_latency_ms)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Calendar agent error: {e}")
        reply = _fallback_plan(progress, tc_inv)

    sessions = _extract_sessions(progress, tc_inv)

    return {
        "plan": reply,
        "sessions": sessions,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ── Event Scheduling ────────────────────────────────────────

async def schedule_event(user_id: str, title: str, event_type: str = "study",
                         date: str | None = None, time: str | None = None,
                         duration_mins: int = 60, description: str = "",
                         ta_id: str | None = None,
                         reminder_minutes_before: int | None = None,
                         notify_at_start: bool | None = None) -> dict:
    """Schedule a calendar event (study session, reminder, deadline, etc.)."""
    prefs = get_prefs(user_id)
    event = {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "title": title,
        "type": event_type,  # study, reminder, deadline, exam, meeting
        "date": date or "TBD",
        "time": time or "TBD",
        "duration_mins": duration_mins,
        "description": description,
        "ta_id": ta_id,
        "status": "scheduled",  # scheduled, completed, cancelled
        "reminder_minutes_before": (reminder_minutes_before
                                    if reminder_minutes_before is not None
                                    else prefs["reminder_minutes_before"]),
        "notify_at_start": (notify_at_start
                            if notify_at_start is not None
                            else prefs["notify_at_start"]),
        "_notified_before": False,
        "_notified_start": False,
        "created_at": datetime.now(UTC).isoformat(),
    }

    _user_events.setdefault(user_id, []).append(event)
    _flush_events_store()

    from app.events.bus import publish
    await publish("event_scheduled", {"user_id": user_id, "event": event})

    return event


async def get_user_events(user_id: str, include_past: bool = False) -> list[dict]:
    """Get all events for a user."""
    events = _user_events.get(user_id, [])
    if not include_past:
        events = [e for e in events if e["status"] != "cancelled"]
    return sorted(events, key=lambda e: e.get("date", "ZZZ"))


async def update_event_status(user_id: str, event_id: str, status: str) -> dict | None:
    """Update event status (completed, cancelled)."""
    events = _user_events.get(user_id, [])
    for event in events:
        if event["id"] == event_id:
            event["status"] = status
            _flush_events_store()
            return event
    return None


async def delete_event(user_id: str, event_id: str) -> bool:
    """Delete an event. Holidays are protected and cannot be deleted."""
    events = _user_events.get(user_id, [])
    for i, event in enumerate(events):
        if event["id"] == event_id:
            if event.get("type") == "holiday":
                logger.info(f"Refused to delete holiday '{event.get('title')}' for user {user_id}")
                return False
            events.pop(i)
            _flush_events_store()
            return True
    return False


# ── Notifications ───────────────────────────────────────────

def _event_start_dt(event: dict) -> datetime | None:
    """Parse event date+time as server-local wall-clock (no tz attached).
    Events are entered in the user's local wall-clock; the App Service is set to
    IST (TZ=Asia/Kolkata) so datetime.now() returns the same frame of reference."""
    date, time = event.get("date"), event.get("time")
    if not date or date == "TBD":
        return None
    time = time if (time and time != "TBD") else "09:00"
    try:
        return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


async def _push_notification(user_id: str, event: dict, kind: str) -> None:
    """Record a notification and publish an event on the bus."""
    note = {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "event_id": event["id"],
        "title": event["title"],
        "kind": kind,  # "reminder" | "starting"
        "when": f"{event.get('date','')} {event.get('time','')}".strip(),
        "read": False,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _notifications.setdefault(user_id, []).append(note)
    # Cap list at 100 per user.
    _notifications[user_id] = _notifications[user_id][-100:]
    _flush_events_store()

    from app.events.bus import publish
    await publish(f"event_{kind}", {"user_id": user_id, "event": event, "notification": note})
    logger.info(f"[notify] {kind} for user={user_id} event={event['id']} ({event['title']})")


async def _scan_events_once() -> int:
    """Single pass over all events — fire reminders whose time has arrived."""
    now = datetime.now()   # naive local wall-clock (TZ set on App Service to Asia/Kolkata)
    fired = 0
    dirty = False
    for user_id, events in list(_user_events.items()):
        for event in events:
            if event.get("status") != "scheduled":
                continue
            start = _event_start_dt(event)
            if not start:
                continue
            rmin = int(event.get("reminder_minutes_before", 15) or 0)
            # Reminder window: rmin before → start
            if rmin > 0 and not event.get("_notified_before"):
                if start - timedelta(minutes=rmin) <= now < start:
                    await _push_notification(user_id, event, "reminder")
                    event["_notified_before"] = True
                    fired += 1
                    dirty = True
            # At-start window: 0 → +2min to avoid double-fires
            if event.get("notify_at_start", True) and not event.get("_notified_start"):
                if start <= now < start + timedelta(minutes=2):
                    await _push_notification(user_id, event, "starting")
                    event["_notified_start"] = True
                    fired += 1
                    dirty = True
    if dirty:
        _flush_events_store()
    return fired


_scan_task: asyncio.Task | None = None


async def _scan_loop(interval_seconds: int = 30):
    while True:
        try:
            await _scan_events_once()
        except Exception as e:
            logger.warning(f"Calendar scan loop error: {e}")
        await asyncio.sleep(interval_seconds)


def start_notification_scanner(interval_seconds: int = 30) -> None:
    """Start the background notification scanner (idempotent)."""
    global _scan_task
    if _scan_task and not _scan_task.done():
        return
    loop = asyncio.get_event_loop()
    _scan_task = loop.create_task(_scan_loop(interval_seconds))
    logger.info(f"Calendar notification scanner started (every {interval_seconds}s)")


def stop_notification_scanner() -> None:
    global _scan_task
    if _scan_task and not _scan_task.done():
        _scan_task.cancel()
    _scan_task = None


def get_notifications(user_id: str, unread_only: bool = False) -> list[dict]:
    notes = _notifications.get(user_id, [])
    if unread_only:
        notes = [n for n in notes if not n["read"]]
    return sorted(notes, key=lambda n: n["created_at"], reverse=True)


def mark_notifications_read(user_id: str, ids: list[str] | None = None) -> int:
    notes = _notifications.get(user_id, [])
    count = 0
    for n in notes:
        if ids is None or n["id"] in ids:
            if not n["read"]:
                n["read"] = True
                count += 1
    if count:
        _flush_events_store()
    return count


EVENT_PARSE_PROMPT = build_agent_prompt(
    role="Calendar Event Parser",
    mission="Extract structured event details from the user's natural-language scheduling message.",
    capabilities=[
        "Parse event title, type, date, time, duration, description, and target TA.",
        "Resolve relative dates and loosely-formatted times into absolute values.",
    ],
    rules=[
        'If the user gives a time but no date (e.g. "at 9.38", "at 3pm"), set date to TODAY.',
        'Convert times with dots like "9.38" → "09:38", "9.38pm" → "21:38".',
        '"tomorrow" / "next monday" / "in 2 days" → compute the absolute date.',
        'If nothing about time is mentioned, keep time null (not "09:00").',
        'Default type is "reminder" unless words like "study", "exam", "deadline", "meeting" appear.',
        "Do NOT make up info the user didn't give.",
        "Output JSON only, no markdown, no prose.",
    ],
    output_format=(
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "title": "short event title",\n'
        '  "type": "study|reminder|deadline|exam|meeting|other",\n'
        '  "date": "YYYY-MM-DD or null if not specified",\n'
        '  "time": "HH:MM (24-hour) or null if not specified",\n'
        '  "duration_mins": 60,\n'
        '  "description": "brief description",\n'
        '  "ta_id": "math-ta or cs-ta or null"\n'
        "}"
    ),
)


def _normalize_time(t: str | None) -> str | None:
    """Normalize common time formats to HH:MM 24-hour. Returns None if unparseable."""
    if not t:
        return None
    s = str(t).strip().lower().replace(" ", "")
    if not s:
        return None
    ampm = None
    if s.endswith("am") or s.endswith("pm"):
        ampm = s[-2:]
        s = s[:-2]
    s = s.replace(".", ":").replace("-", ":")
    if ":" not in s and s.isdigit():
        s = s + ":00" if len(s) <= 2 else f"{s[:-2]}:{s[-2:]}"
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mm < 60):
        return None
    return f"{h:02d}:{mm:02d}"


async def parse_and_schedule(user_id: str, message: str) -> dict:
    """Use LLM to parse natural language into a calendar event, then schedule it."""
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    prompt = f"{EVENT_PARSE_PROMPT}\n\nToday's date: {today}\nCurrent time (UTC): {now.strftime('%H:%M')}"

    client = _get_client()
    agent = client.as_agent(name="EventParser", instructions=prompt)

    try:
        result = await agent.run(message)
        raw = str(result).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        data = json.loads(raw.strip())
    except Exception as e:
        logger.warning(f"Event parse failed: {e}")
        # Fallback: create a basic event from the message
        data = {
            "title": message[:50],
            "type": "reminder",
            "date": None,
            "time": None,
            "duration_mins": 60,
            "description": message,
            "ta_id": None,
        }

    # Post-process: normalize time, default date when only time was given
    normalized_time = _normalize_time(data.get("time"))
    if normalized_time:
        data["time"] = normalized_time
    elif data.get("time") in (None, "", "null", "TBD"):
        data["time"] = None

    if not data.get("date") or data.get("date") in ("null", "TBD"):
        if data.get("time"):
            # Time given but no date → today (if future) else tomorrow
            try:
                evt_dt = datetime.strptime(f"{today} {data['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
                if evt_dt <= now:
                    data["date"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                else:
                    data["date"] = today
            except Exception:
                data["date"] = today
        else:
            data["date"] = None

    event = await schedule_event(
        user_id=user_id,
        title=data.get("title") or message[:50],
        event_type=data.get("type") or "reminder",
        date=data.get("date"),
        time=data.get("time"),
        duration_mins=data.get("duration_mins") or 60,
        description=data.get("description") or "",
        ta_id=data.get("ta_id"),
    )

    # Build a friendly response
    lines = [f"Scheduled: {event['title']}"]
    if event["date"] and event["date"] != "TBD":
        lines.append(f"Date: {event['date']}")
    if event["time"] and event["time"] != "TBD":
        lines.append(f"Time: {event['time']}")
    if event["duration_mins"] != 60:
        lines.append(f"Duration: {event['duration_mins']} min")
    lines.append(f"Type: {event['type']}")
    remind = event.get("reminder_minutes_before")
    if remind:
        lines.append(f"Reminder: {remind} min before")
    if event["ta_id"]:
        lines.append(f"TA: {event['ta_id']}")

    return {
        "reply": "\n".join(lines),
        "event": event,
    }


# ── Helpers ──────────────────────────────────────────────────

def _fallback_plan(progress: dict, tc_inv: dict) -> str:
    """Simple rule-based plan when LLM fails."""
    lines = ["Study Plan:\n"]
    in_progress = tc_inv.get("in_progress", [])

    if in_progress:
        sorted_tcs = sorted(in_progress, key=lambda t: t.get("progress_pct", 0))
        for i, tc in enumerate(sorted_tcs[:3], 1):
            lines.append(f"Session {i}: Focus on {tc['tc_id'].replace('-', ' ')} ({tc.get('progress_pct', 0)}% complete)")
    else:
        lines.append("Session 1: Start with Mathematics TA — Level 1 Foundations")
        lines.append("Session 2: Start with CS TA — Level 1 Basics")

    return "\n".join(lines)


def _extract_sessions(progress: dict, tc_inv: dict) -> list[dict]:
    """Build structured session list from progress data."""
    sessions = []
    in_progress = tc_inv.get("in_progress", [])

    for tc in sorted(in_progress, key=lambda t: t.get("progress_pct", 0)):
        ta_id = "math-ta" if tc["tc_id"].startswith("math-") else "cs-ta"
        sessions.append({
            "topic": tc["tc_id"].replace("-", " ").title(),
            "ta_id": ta_id,
            "priority": "high" if tc.get("progress_pct", 0) > 50 else "medium",
            "current_pct": tc.get("progress_pct", 0),
        })

    for ta_id, data in progress.items():
        level = data.get("current_level", 1)
        module = data.get("current_module", "")
        if module:
            sessions.append({
                "topic": f"{module} (Level {level})",
                "ta_id": ta_id,
                "priority": "normal",
                "current_pct": None,
            })

    return sessions[:5]


def get_agent_card(base_url: str = "") -> "AgentCard":
    from app.protocols.models import AgentCard, AgentProvider, AgentInterface, AgentCapabilities, AgentSkill
    return AgentCard(
        name="Calendar Agent",
        description="Study plan generation, event scheduling, and calendar management. Tracks study sessions and sends reminders.",
        version="1.0.0",
        documentationUrl=f"{base_url}/docs/calendar",
        provider=AgentProvider(organization="Lumen Network", url=base_url),
        supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/calendar")],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=True),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain", "application/json"],
        securitySchemes={
            "lumenJwt": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT", "description": "Lumen JWT"}}
        },
        securityRequirements=[{"lumenJwt": []}],
        skills=[
            AgentSkill(
                id="calendar.generate_study_plan",
                name="Generate Study Plan",
                description="Generate a personalized weekly study plan based on progress gaps and threshold concepts in-progress",
                tags=["study-plan", "schedule", "curriculum", "planning"],
                examples=["Create a study plan for me", "Plan my weak areas this week", "Generate a schedule for linear algebra"],
            ),
            AgentSkill(
                id="calendar.schedule_event",
                name="Schedule Event",
                description="Schedule a specific study session or event on the calendar",
                tags=["schedule", "event", "calendar", "session"],
                examples=["Schedule a calculus session Friday 3pm", "Add a study block tomorrow morning", "Book a session for recursion practice"],
            ),
            AgentSkill(
                id="calendar.get_events",
                name="Get Events",
                description="Retrieve upcoming study sessions and calendar events",
                tags=["events", "calendar", "upcoming", "schedule"],
                examples=["What's on my calendar?", "Show my upcoming sessions", "What do I have this week?"],
            ),
            AgentSkill(
                id="calendar.parse_and_schedule",
                name="Natural Language Scheduling",
                description="Parse a natural language scheduling request and create the event",
                tags=["nlp", "schedule", "natural-language"],
                examples=["Remind me to study linear algebra on Monday", "Schedule 2 hours of calculus every morning this week"],
            ),
        ],
    )
