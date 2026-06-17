"""Google Calendar agent — list/search/create/delete events via Calendar v3 API.

Reuses google_config storage from gmail_agent (same OAuth grant covers all three
Google services).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

CAL_API = "https://www.googleapis.com/calendar/v3"


# ── HTTP helper ──────────────────────────────────────────────────────────────

async def _request(token: str, method: str, path: str,
                    params: dict | None = None, json: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{CAL_API}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=headers, params=params, json=json)
        if resp.status_code >= 400:
            logger.warning(f"GCal {method} {path} -> {resp.status_code}: {resp.text[:300]}")
            return {"error": resp.text, "status": resp.status_code}
        return resp.json() if resp.text else {}


# ── Format helpers ───────────────────────────────────────────────────────────

def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _event_summary(ev: dict) -> dict:
    """Compact event dict for chat cards."""
    start = ev.get("start", {})
    end = ev.get("end", {})
    start_iso = start.get("dateTime") or start.get("date") or ""
    end_iso = end.get("dateTime") or end.get("date") or ""
    return {
        "id": ev.get("id"),
        "title": ev.get("summary", "(no title)"),
        "description": ev.get("description", ""),
        "location": ev.get("location", ""),
        "start": start_iso,
        "end": end_iso,
        "all_day": "date" in start,
        "url": ev.get("htmlLink"),
        "attendees": [a.get("email") for a in (ev.get("attendees") or []) if a.get("email")],
        "calendar_id": ev.get("organizer", {}).get("email", "primary"),
    }


# ── List / search ────────────────────────────────────────────────────────────

async def list_events(token: str, days_ahead: int = 7, max_results: int = 20,
                       calendar_id: str = "primary") -> list[dict]:
    """List upcoming events for the next `days_ahead` days."""
    now = datetime.now(timezone.utc)
    time_min = _iso_utc(now)
    time_max = _iso_utc(now + timedelta(days=days_ahead))
    res = await _request(token, "GET", f"/calendars/{calendar_id}/events", params={
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max_results,
    })
    if "error" in res:
        return []
    return [_event_summary(e) for e in res.get("items", [])]


async def search_events(token: str, query: str, days_ahead: int = 90,
                        max_results: int = 20, calendar_id: str = "primary") -> list[dict]:
    """Free-text search across the user's calendar."""
    now = datetime.now(timezone.utc)
    time_min = _iso_utc(now - timedelta(days=7))
    time_max = _iso_utc(now + timedelta(days=days_ahead))
    res = await _request(token, "GET", f"/calendars/{calendar_id}/events", params={
        "q": query,
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max_results,
    })
    if "error" in res:
        return []
    return [_event_summary(e) for e in res.get("items", [])]


async def get_event(token: str, event_id: str, calendar_id: str = "primary") -> dict:
    res = await _request(token, "GET", f"/calendars/{calendar_id}/events/{event_id}")
    if "error" in res:
        return {"error": res.get("error", "Could not fetch event")}
    return _event_summary(res)


# ── Create / delete ──────────────────────────────────────────────────────────

async def create_event(token: str, title: str,
                        start: datetime | str,
                        end: datetime | str | None = None,
                        description: str = "",
                        location: str = "",
                        attendees: list[str] | None = None,
                        all_day: bool = False,
                        calendar_id: str = "primary") -> dict:
    """Create a calendar event.

    - `start` / `end` accept either ISO strings (with timezone) or datetime objects.
    - If `end` is None and not all_day, defaults to start + 1 hour.
    - If `all_day=True`, uses YYYY-MM-DD `date` fields instead of `dateTime`.
    """
    if not title:
        return {"error": "Title is required"}

    body: dict = {"summary": title}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees if e]

    if all_day:
        # Accept either a date string or datetime
        if isinstance(start, datetime):
            start_str = start.date().isoformat()
        else:
            start_str = str(start)[:10]
        if end is None:
            # Single-day event — Google requires end = start + 1 day for all-day
            start_dt = datetime.fromisoformat(start_str)
            end_str = (start_dt + timedelta(days=1)).date().isoformat()
        elif isinstance(end, datetime):
            end_str = end.date().isoformat()
        else:
            end_str = str(end)[:10]
        body["start"] = {"date": start_str}
        body["end"] = {"date": end_str}
    else:
        if isinstance(start, datetime):
            start_iso = _iso_utc(start)
        else:
            start_iso = str(start)
        if end is None:
            if isinstance(start, datetime):
                end_iso = _iso_utc(start + timedelta(hours=1))
            else:
                # Parse the ISO string and add an hour
                try:
                    dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                    end_iso = _iso_utc(dt + timedelta(hours=1))
                except Exception:
                    end_iso = start_iso
        elif isinstance(end, datetime):
            end_iso = _iso_utc(end)
        else:
            end_iso = str(end)
        body["start"] = {"dateTime": start_iso}
        body["end"] = {"dateTime": end_iso}

    res = await _request(token, "POST", f"/calendars/{calendar_id}/events", json=body)
    if "error" in res:
        return {"error": res.get("error", "Could not create event")}
    return _event_summary(res)


async def delete_event(token: str, event_id: str, calendar_id: str = "primary") -> dict:
    res = await _request(token, "DELETE", f"/calendars/{calendar_id}/events/{event_id}")
    if "error" in res:
        return {"error": res.get("error", "Could not delete event"), "ok": False}
    return {"ok": True, "id": event_id}


# ── Natural-language parsing ─────────────────────────────────────────────────

def parse_when(text: str) -> tuple[datetime, datetime, bool] | None:
    """Very light natural-language → (start, end, all_day) parser.

    Handles: 'tomorrow at 3pm', 'today 9am', 'next monday at 10am', 'friday 2pm',
    'at 3pm', date-only ('on monday'), etc. Returns None if it can't infer.
    """
    import re as _re
    text_l = text.lower()
    now = datetime.now(timezone.utc).astimezone()

    # Day-of-week mapping
    DOW = {
        "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2, "wed": 2,
        "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4,
        "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
    }

    # Decide the day
    target_date = None
    if "tomorrow" in text_l:
        target_date = (now + timedelta(days=1)).date()
    elif "today" in text_l or "tonight" in text_l:
        target_date = now.date()
    elif "day after tomorrow" in text_l:
        target_date = (now + timedelta(days=2)).date()
    else:
        # Month-name date: "june 12", "12 june", "december 25th", "jan 1"
        MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
                  "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
                  "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
                  "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
        month_pat = "|".join(MONTHS.keys())
        # "june 12" / "june 12th" / "june 12,"
        m = _re.search(rf"\b({month_pat})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text_l)
        if not m:
            # "12 june" / "12th june"
            m = _re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pat})\b", text_l)
            if m:
                day = int(m.group(1))
                month = MONTHS[m.group(2)]
            else:
                day = month = None
        else:
            month = MONTHS[m.group(1)]
            day = int(m.group(2))
        if month and day:
            year = now.year
            try:
                candidate = now.replace(month=month, day=day, hour=0, minute=0,
                                          second=0, microsecond=0).date()
                # If the date already passed this year, roll to next year
                if candidate < now.date():
                    candidate = candidate.replace(year=year + 1)
                target_date = candidate
            except ValueError:
                pass

        # Day-of-week fallback if no month-name date matched
        if target_date is None:
            for name, idx in DOW.items():
                if _re.search(rf"\b{name}\b", text_l):
                    offset = (idx - now.weekday()) % 7
                    if "next" in text_l and offset == 0:
                        offset = 7
                    if offset == 0:
                        offset = 7
                    target_date = (now + timedelta(days=offset)).date()
                    break

    # Decide the time
    time_match = _re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text_l)
    hour = None
    minute = 0
    if time_match:
        hour = int(time_match.group(1))
        if time_match.group(2):
            minute = int(time_match.group(2))
        ampm = time_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif ampm is None and hour < 8:
            # Bare hour like "at 3" — assume PM if it's reasonable
            hour += 12

    if target_date is None and hour is None:
        return None
    if target_date is None:
        target_date = now.date()

    if hour is None:
        # All-day event
        start = datetime.combine(target_date, datetime.min.time(), tzinfo=now.tzinfo)
        end = start + timedelta(days=1)
        return start, end, True

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=now.tzinfo).replace(
        hour=hour, minute=minute
    )
    end = start + timedelta(hours=1)
    return start, end, False
