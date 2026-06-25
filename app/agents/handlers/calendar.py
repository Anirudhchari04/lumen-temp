"""Calendar agent — Lumen calendar + Google Calendar query/create/reschedule. Class-based: `CalendarAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

import logging

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.agents.a2a_client import a2a_tasks_send
from app.agents.calendar_agent import (
    generate_study_plan,
    parse_and_schedule,
    schedule_event,
    get_prefs,
)
from app.agents.handlers._common import _ensure_intent
from app.agents.state import _pending_proposals

logger = logging.getLogger(__name__)


class CalendarAgent(BaseAgent):
    name = "calendar"
    intents = (Intent.QUERY, Intent.SCHEDULING)
    description = "Calendar + Google Calendar query/create/reschedule"
    # Offline keyword fallbacks owned by this agent (the LLM router is primary).
    MANAGE_KEYWORDS = (
        "cancel ", "remove ", "delete ", "postpone", "reschedule",
        "move meeting", "move event", "push back",
    )
    QUERY_KEYWORDS = (
        "what's on", "what\u2019s on", "what is on", "my events", "my schedule",
        "what's scheduled", "what\u2019s scheduled",
        "upcoming", "what do i have", "show my calendar", "show calendar",
        "events this", "events in", "events for", "monthly schedule",
        "this month", "next month", "this week", "next week",
        "my calendar", "on my calendar", "calendar events",
    )
    SCHEDULE_KEYWORDS = (
        "study plan", "schedule", "plan my", "what order", "when should",
        "routine", "plan for me", "study schedule",
        "remind me", "set a reminder", "deadline", "exam on",
        "add holiday", "add event", "mark as holiday",
    )

    async def handle_google_calendar(self, user_id: str, message: str) -> dict | None:
        """If the user has Google Calendar connected, handle read/create/delete via the
        Google Calendar API. Returns a response dict, or None to fall through to the
        Lumen calendar agent (used for study plans, holidays, and other Lumen-specific
        features that don't map to Google Calendar).
        """
        from app.agents.gmail_agent import is_gcalendar_connected, get_valid_google_token
        from app.agents.gcalendar_agent import (
            list_events, search_events, create_event, delete_event, parse_when,
        )
        from app.lumen.core import get_lumen

        lumen = await get_lumen(user_id)
        if not is_gcalendar_connected(lumen):
            return None  # fall through to Lumen calendar

        # Honor the user's preferred calendar provider (set in Profile).
        # Default behavior when unset / "auto" / "google" → use Google Calendar (current path).
        pref = (lumen.get("preferences", {}) or {}).get("calendar_provider", "auto") or "auto"
        pref = pref.lower()
        if pref in ("lumen", "local"):
            return None  # fall through to Lumen's internal calendar
        if pref == "outlook":
            return None  # let the existing Outlook handler take it

        # User can also opt out per-message by saying "lumen calendar"
        msg = (message or "").lower().strip()
        if "lumen calendar" in msg or "local calendar" in msg or "internal calendar" in msg:
            return None
        if "outlook calendar" in msg:
            return None

        # Skip Google Calendar for Lumen-specific features (study plans, holidays).
        LUMEN_ONLY_KW = ["study plan", "plan my week", "plan for me", "study schedule",
                         "add holiday", "mark as holiday", "build a plan"]
        if any(kw in msg for kw in LUMEN_ONLY_KW):
            return None

        token = await get_valid_google_token(user_id)
        if not token:
            return None

        import re as _re_gc
        from datetime import datetime, timedelta, timezone

        # ── DELETE ──
        delete_kw = ["cancel ", "delete ", "remove ", "drop "]
        if any(kw in msg for kw in delete_kw):
            # Find what to delete — strip the verb + filler words
            hint = msg
            for kw in delete_kw:
                if kw in hint:
                    hint = hint.split(kw, 1)[-1].strip()
                    break
            for drop in ("event", "events", "meeting", "meetings", "reminder", "reminders",
                          "the", "my", "all", "from", "on", "in", "calendar", "google", "gcal"):
                hint = _re_gc.sub(rf"\b{drop}\b", "", hint).strip()
            hint = _re_gc.sub(r"\s+", " ", hint).strip()
            if not hint:
                return {
                    "reply": "Which event? Say e.g. *delete my 3pm meeting* or *cancel chemistry class*.",
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "gcalendar",
                }
            matches = await search_events(token, hint, days_ahead=90)
            if not matches:
                return {
                    "reply": f"📅 No events matching *{hint}* found in your Google Calendar.",
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "gcalendar",
                }
            # Delete the closest upcoming match (or all if "all")
            targets = matches if "all" in msg else matches[:1]
            deleted = []
            for ev in targets:
                r = await delete_event(token, ev["id"])
                if r.get("ok"):
                    deleted.append(ev.get("title", "(no title)"))
            if not deleted:
                return {
                    "reply": f"⚠ Could not delete events matching *{hint}*.",
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "gcalendar",
                }
            titles = ", ".join(deleted)
            return {
                "reply": f"🗑 Deleted from Google Calendar: **{titles}**.",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "gcalendar",
            }

        # ── CREATE ──
        # Patterns: "add event X at Y", "schedule X for tomorrow", "remind me to X at 3pm",
        # "create event X", "set a reminder for X at Y", "book X tomorrow 5pm",
        # "set june 12 as my birthday on my calendar", "mark holi on my calendar".
        create_kw = ["add event", "add a meeting", "add meeting", "add to calendar",
                     "schedule", "remind me", "set a reminder", "create event",
                     "create a meeting", "book ", "new event", "new meeting",
                     "mark "]
        # Pattern fallback — verb + (calendar OR event noun)
        _create_pat = _re_gc.compile(
            r"\b(set|add|mark|block|book|create|put|insert|schedule|new)\b"
            r".*?\b(calendar|event|birthday|anniversary|reminder|meeting|"
            r"appointment|slot|holiday|deadline)\b",
            _re_gc.IGNORECASE,
        )
        is_create = any(kw in msg for kw in create_kw) or bool(_create_pat.search(msg))
        if is_create:
            # Extract title — strip the verb prefix
            title_text = message
            for kw in ["add an event", "add a meeting", "add event", "add meeting",
                       "schedule a meeting", "schedule a", "schedule",
                       "remind me to", "remind me",
                       "set a reminder for", "set a reminder", "set ",
                       "mark ",
                       "create an event", "create a meeting", "create event", "create",
                       "new event", "new meeting", "book ", "add to calendar"]:
                if title_text.lower().startswith(kw):
                    title_text = title_text[len(kw):].strip()
                    break
            # Drop leading articles
            title_text = _re_gc.sub(r"^(?:to|a|an|the)\s+", "", title_text, flags=_re_gc.IGNORECASE)
            # If the message follows the "X as my <birthday|...>" template, the
            # ACTUAL title is the noun after "as my", and the date is what came before.
            # e.g. "june 12 as my birthday on my google calendar" → title="Birthday", date="june 12"
            as_my_match = _re_gc.search(
                r"^(.+?)\s+as\s+(?:my|the)\s+(.+?)(?:\s+(?:on|in|to)\s+(?:my\s+)?(?:google\s+)?calendar)?$",
                title_text, _re_gc.IGNORECASE,
            )
            date_hint_from_as_my = None
            if as_my_match:
                date_hint_from_as_my = as_my_match.group(1).strip()
                title_text = as_my_match.group(2).strip()

            # Detect natural-language time. Prefer the "as my" date hint if present,
            # then the rest of the message.
            when = None
            if date_hint_from_as_my:
                when = parse_when(date_hint_from_as_my)
            if when is None:
                when = parse_when(message)

            # Strip the when-portion from the title for a cleaner summary
            title = title_text
            for cue in [" at ", " on ", " tomorrow", " today", " tonight",
                        " next ", " this ", " for ", " from "]:
                idx = title.lower().find(cue)
                if idx > 0:
                    title = title[:idx].strip()
                    break
            title = title.strip(".,;:!?\"' ") or "Untitled event"

            if when is None:
                # Default: tomorrow 10am, 1h block
                start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                    hour=10, minute=0, second=0, microsecond=0
                ).astimezone()
                end = start + timedelta(hours=1)
                all_day = False
            else:
                start, end, all_day = when

            result = await create_event(token, title=title, start=start, end=end, all_day=all_day)
            if result.get("error"):
                return {
                    "reply": f"⚠ Couldn't create the event: {result['error']}",
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "gcalendar",
                }
            when_str = start.strftime("%a %b %d") + ("" if all_day else f" at {start.strftime('%I:%M %p').lstrip('0')}")
            return {
                "reply": (
                    f"✅ Added **{title}** to your Google Calendar — {when_str}"
                    + (f" — [open]({result.get('url', '')})" if result.get("url") else "")
                ),
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "gcalendar",
            }

        # ── SEARCH ──
        search_match = _re_gc.search(
            r"(?:search|find|look\s+for)\s+(?:my\s+)?(?:event|meeting|calendar)?s?"
            r"\s+(?:about|on|for|with|containing|regarding)?\s+(.+?)(?:\?|\.|$)",
            msg,
        )
        if search_match or any(kw in msg for kw in ["search calendar", "find event", "find meeting"]):
            query = (search_match.group(1) if search_match else "").strip().strip('"\'')
            events = await search_events(token, query or "", days_ahead=60) if query else \
                      await list_events(token, days_ahead=30, max_results=20)
            if not events:
                return {
                    "reply": f"📅 No matching events found in Google Calendar.",
                    "action": "inline_answer",
                    "intent": Intent.QUERY,
                    "agent_id": "gcalendar",
                }
            lines = [f"📅 **{len(events)} event(s){' matching *' + query + '*' if query else ''}:**\n"]
            for ev in events[:5]:
                when_str = ev.get("start", "")[:16].replace("T", " ")
                lines.append(f"- **{ev.get('title')}** — {when_str}")
            return {
                "reply": "\n".join(lines),
                "action": "calendar_query",
                "intent": Intent.QUERY,
                "agent_id": "gcalendar",
                "cards": [{"type": "gcal_events", "data": events[:10]}],
            }

        # ── DEFAULT QUERY: today / tomorrow / this week / upcoming ──
        days_ahead = 7
        if "today" in msg or "tonight" in msg:
            days_ahead = 1
        elif "tomorrow" in msg:
            days_ahead = 2
        elif "this week" in msg or "next week" in msg:
            days_ahead = 7
        elif "this month" in msg or "next month" in msg:
            days_ahead = 31
        events = await list_events(token, days_ahead=days_ahead, max_results=20)

        # Filter to specific day if asked
        if "today" in msg or "tonight" in msg:
            today_iso = datetime.now().astimezone().date().isoformat()
            events = [e for e in events if (e.get("start") or "").startswith(today_iso)]
        elif "tomorrow" in msg:
            tomorrow_iso = (datetime.now().astimezone() + timedelta(days=1)).date().isoformat()
            events = [e for e in events if (e.get("start") or "").startswith(tomorrow_iso)]

        if not events:
            return {
                "reply": "📅 No events in your Google Calendar for that range.",
                "action": "inline_answer",
                "intent": Intent.QUERY,
                "agent_id": "gcalendar",
            }
        label = "today" if "today" in msg else ("tomorrow" if "tomorrow" in msg
                else (f"the next {days_ahead} day(s)" if days_ahead > 1 else "today"))
        lines = [f"📅 **{len(events)} event(s)** in {label}:\n"]
        for ev in events[:5]:
            when_str = ev.get("start", "")[:16].replace("T", " ")
            lines.append(f"- **{ev.get('title')}** — {when_str}")
        return {
            "reply": "\n".join(lines),
            "action": "calendar_query",
            "intent": Intent.QUERY,
            "agent_id": "gcalendar",
            "cards": [{"type": "gcal_events", "data": events[:10]}],
        }

    async def handle_calendar_query(self, user_id: str, message: str = "") -> dict:
        """Query calendar events and return inline. Supports month/week/type filtering."""
        # Prefer Google Calendar if the user has it connected
        gcal_result = await self.handle_google_calendar(user_id, message)
        if gcal_result is not None:
            return gcal_result

        from app.agents.calendar_agent import get_user_events, seed_holidays
        seed_holidays(user_id)  # Ensure holidays are present
        events = await get_user_events(user_id, include_past=True)
        msg = (message or "").lower()

        # Filter by month
        months = {"january": "01", "february": "02", "march": "03", "april": "04",
                  "may": "05", "june": "06", "july": "07", "august": "08",
                  "september": "09", "october": "10", "november": "11", "december": "12",
                  "jan": "01", "feb": "02", "mar": "03", "apr": "04",
                  "jun": "06", "jul": "07", "aug": "08", "sep": "09",
                  "oct": "10", "nov": "11", "dec": "12"}
        month_filter = None
        for mname, mnum in months.items():
            if mname in msg:
                month_filter = mnum
                break

        # Filter by type
        type_filter = None
        if "holiday" in msg or "holidays" in msg:
            type_filter = "holiday"
        elif "study" in msg:
            type_filter = "study"
        elif "meeting" in msg:
            type_filter = "meeting"
        elif "reminder" in msg:
            type_filter = "reminder"

        filtered = events
        if month_filter:
            filtered = [e for e in filtered if e.get("date", "").split("-")[1:2] == [month_filter]]
        if type_filter:
            filtered = [e for e in filtered if e.get("type", "") == type_filter]

        # Today / tomorrow filter
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        if "today" in msg:
            filtered = [e for e in events if e.get("date") == today]
        elif "tomorrow" in msg:
            filtered = [e for e in events if e.get("date") == tomorrow]
        elif "this week" in msg:
            from datetime import date as dt_date
            d = dt_date.today()
            start = d - timedelta(days=d.weekday())
            end = start + timedelta(days=6)
            filtered = [e for e in events if start.isoformat() <= e.get("date", "") <= end.isoformat()]
        elif not month_filter and not type_filter and "today" not in msg and "tomorrow" not in msg:
            # Default: upcoming only (future events)
            filtered = [e for e in events if e.get("date", "") >= today]

        if not filtered:
            label = ""
            if month_filter:
                _month_names = [k for k, v in months.items() if v == month_filter and len(k) > 3]
                label = f" in {_month_names[0].title()}" if _month_names else ""
            if type_filter:
                label += f" ({type_filter})"
            reply = f"📅 No events found{label}. Say 'remind me...' or 'schedule...' to add one."
        else:
            label = ""
            if month_filter:
                long_months = {v: k.title() for k, v in months.items() if len(k) > 3}
                label = f" in {long_months.get(month_filter, '')}"
            if type_filter:
                label += f" ({type_filter})"
            lines = [f"📅 **Your events{label}** ({len(filtered)}):\n"]
            for ev in filtered[:10]:
                date = ev.get("date", "TBD")
                time = ev.get("time", "")
                title = ev.get("title", "Event")
                status = ev.get("status", "scheduled")
                etype = ev.get("type", "")
                time_str = f" at {time}" if time and time != "TBD" else ""
                type_tag = f" [{etype}]" if etype and etype not in ("event", "study") else ""
                lines.append(f"- **{title}** — {date}{time_str}{type_tag} ({status})")
            if len(filtered) > 10:
                lines.append(f"\n...and {len(filtered) - 10} more.")
            reply = "\n".join(lines)

        event_cards = [{
            "type": "events",
            "data": [{"id": e.get("id", ""), "title": e.get("title", ""), "date": e.get("date", ""),
                      "time": e.get("time", ""), "status": e.get("status", "scheduled"),
                      "type": e.get("type", "event")} for e in filtered[:10]],
        }]

        # Generate A2UI calendar view
        from datetime import datetime as _dt
        now_dt = _dt.now()
        cal_events = [{"date": e.get("date", ""), "label": e.get("title", ""), "tone": "success" if e.get("type") == "holiday" else None} for e in filtered[:15]]
        a2ui_doc = {
            "surface": "chat",
            "root": "cal-root",
            "components": [
                {"id": "cal-root", "type": "Card", "props": {"variant": "outlined"}, "children": ["cal-heading", "cal-widget", "cal-table"]},
                {"id": "cal-heading", "type": "Heading", "props": {"text": f"Calendar{label}", "level": 3}},
                {"id": "cal-widget", "type": "Calendar", "props": {"year": now_dt.year, "month": now_dt.month, "events": cal_events}},
                {"id": "cal-table", "type": "Table", "props": {"columns": ["Event", "Date", "Type"], "rows": [[e.get("title", ""), e.get("date", ""), e.get("type", "")] for e in filtered[:10]]}},
            ],
        }

        return {
            "reply": reply,
            "action": "inline_answer",
            "intent": Intent.QUERY,
            "agent_id": "calendar",
            "cards": event_cards,
            "a2ui": a2ui_doc,
        }

    async def handle_scheduling(self, user_id: str, message: str = "") -> dict:
        """Handle scheduling intents.

        - "remind me…"/"schedule a…" → create the event immediately.
        - "study plan" / "plan my week" → build a proposal; user confirms Yes/No before scheduling.
        - "cancel/delete/remove event X" → delete event by title match.
        - "postpone X" → delete + reschedule hint.
        - "add holiday" → schedule as holiday type.
        """
        # Prefer Google Calendar if connected (skips for study-plan / holiday flows
        # which are Lumen-specific — those fall through to the original logic below).
        gcal_result = await self.handle_google_calendar(user_id, message)
        if gcal_result is not None:
            return gcal_result

        from app.agents.calendar_agent import get_user_events, delete_event as cal_delete
        msg = (message or "").lower()

        # Cancel / delete / remove events — works with any phrasing:
        # "remove Holi", "cancel all events today", "delete the 3pm study", "remove them all"
        cancel_kw = ["cancel ", "delete ", "remove "]
        for ckw in cancel_kw:
            if ckw not in msg:
                continue
            events = await get_user_events(user_id)
            hint = msg.split(ckw, 1)[-1].strip()

            # Strip filler words
            for drop in ("event", "events", "meeting", "meetings", "reminder", "reminders",
                          "the", "my", "all", "from", "on", "in", "calendar"):
                hint = hint.replace(drop, "").strip()

            # Date-based: "today", "tomorrow"
            from datetime import datetime, timedelta
            today_str = datetime.now().strftime("%Y-%m-%d")
            tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

            if "today" in msg:
                match = [e for e in events if e.get("date") == today_str and e.get("type") != "holiday"]
            elif "tomorrow" in msg:
                match = [e for e in events if e.get("date") == tomorrow_str and e.get("type") != "holiday"]
            elif hint in ("", "them", "them all", "everything"):
                # "remove them all" / "remove all" — remove all scheduled events
                match = [e for e in events if e.get("status") == "scheduled" and e.get("type") != "holiday"]
            elif hint:
                # Title match — holidays are protected and excluded
                candidates = [e for e in events if hint in e.get("title", "").lower()]
                holiday_hits = [e for e in candidates if e.get("type") == "holiday"]
                match = [e for e in candidates if e.get("type") != "holiday"]
                if holiday_hits and not match:
                    titles = ", ".join(e.get("title", "?") for e in holiday_hits)
                    return {
                        "reply": f"🎉 **{titles}** is a holiday and can't be removed. Holidays are fixed on the calendar.",
                        "action": "inline_answer",
                        "intent": Intent.SCHEDULING,
                        "agent_id": "calendar",
                    }
            else:
                match = []

            if match:
                for m in match:
                    await cal_delete(user_id, m["id"])
                titles = ", ".join(m.get("title", "?") for m in match[:5])
                extra = f" ...and {len(match) - 5} more" if len(match) > 5 else ""
                return {
                    "reply": f"Removed **{len(match)}** event(s): {titles}{extra}",
                    "action": "event_deleted",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }
            return {
                "reply": "I couldn't find matching events. Say 'what's on my calendar' to see your events.",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }

        # Postpone / reschedule
        if "postpone" in msg or "reschedule" in msg or "push back" in msg or "move " in msg:
            events = await get_user_events(user_id)
            hint = msg
            for drop in ("postpone", "reschedule", "push back", "move", "event", "meeting", "the", "my"):
                hint = hint.replace(drop, "").strip()
            match = [e for e in events if hint and hint in e.get("title", "").lower()] if hint else []
            if match:
                ev = match[0]
                return {
                    "reply": (f"To reschedule **{ev['title']}** (currently {ev.get('date', '?')} "
                              f"at {ev.get('time', '?')}), tell me the new date/time.\n"
                              f"E.g. 'schedule {ev['title']} on 2026-05-01 at 3pm'"),
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }
            return {
                "reply": "Which event do you want to reschedule? Say 'what's on my calendar' to see your events.",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }

        # Add holiday
        if "holiday" in msg or "mark as holiday" in msg:
            try:
                result = await parse_and_schedule(user_id, message)
                event = result.get("event", {})
                event["type"] = "holiday"
                return {
                    "reply": f"🎉 **Holiday added:** {event.get('title', message)} on {event.get('date', 'TBD')}",
                    "action": "event_scheduled",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                    "event": event,
                }
            except Exception:
                pass

        create_kw = ["remind me", "reminder", "set a reminder", "deadline",
                     "exam on", "schedule a", "schedule ", "book ", "add to calendar",
                     "add to my calendar", "put on my calendar", "on my calendar",
                     "add event", "add meeting", "schedule meeting"]
        if any(kw in msg for kw in create_kw):
            try:
                result = await parse_and_schedule(user_id, message)
                event = result.get("event", {})
                title = event.get("title", message)
                date = event.get("date", "TBD")
                time = event.get("time", "TBD")

                # If date/time are TBD, ask the user for details instead of creating a vague event
                if date == "TBD" or time == "TBD":
                    return {
                        "reply": (f"I'd like to schedule **{title}** for you. "
                                  f"When should it be?\n\n"
                                  f"Try: 'schedule {title} on May 5 at 3pm'"),
                        "action": "inline_answer",
                        "intent": Intent.SCHEDULING,
                        "agent_id": "calendar",
                    }

                reply_lines = [f"📅 **Added to your calendar:** {title}"]
                when = date + (f" at {time}" if time not in (None, "TBD") else "")
                reply_lines.append(f"- **When:** {when}")
                rmin = event.get("reminder_minutes_before")
                if rmin:
                    reply_lines.append(f"- **Reminder:** {rmin} min before + at start")
                reply_lines.append("\nCheck your Calendar tab to view all events.")
                return {
                    "reply": "\n".join(reply_lines),
                    "action": "event_scheduled",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                    "event": event,
                }
            except Exception as e:
                return {
                    "reply": f"I had trouble creating that event ({e}). Try the Calendar tab to add it manually.",
                    "action": "scheduling_error",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }

        # Study-plan proposal flow
        plan_kw = ["study plan", "plan my", "make a plan", "plan for me", "study schedule",
                   "what should i study", "what order", "when should"]
        if any(kw in msg for kw in plan_kw):
            try:
                plan = await generate_study_plan(user_id)
                sessions = plan.get("sessions", [])[:4]
                if not sessions:
                    return {
                        "reply": "I can't build a plan yet — start a session with one of the TAs first so I know where you are.",
                        "action": "study_plan_empty",
                        "intent": Intent.SCHEDULING,
                        "agent_id": "calendar",
                    }

                from datetime import datetime, timedelta, timezone as _tz
                UTC = _tz.utc
                prefs = get_prefs(user_id)
                remind = prefs.get("reminder_minutes_before", 15)
                base = datetime.now(UTC) + timedelta(days=1)
                base = base.replace(hour=17, minute=0, second=0, microsecond=0)
                proposal = []
                for i, s in enumerate(sessions):
                    when = base + timedelta(days=i)
                    proposal.append({
                        "title": f"Study: {s.get('topic', 'session')}",
                        "date": when.strftime("%Y-%m-%d"),
                        "time": "17:00",
                        "duration_mins": 60,
                        "type": "study",
                        "ta_id": s.get("ta_id"),
                        "description": f"Focus on {s.get('topic','')}. Priority: {s.get('priority','normal')}.",
                        "reminder_minutes_before": remind,
                    })

                reply_lines = ["Here's a plan based on where you are — want me to add these to your calendar?\n"]
                for p in proposal:
                    reply_lines.append(f"- **{p['title']}** — {p['date']} at {p['time']} (reminds {remind}m before)")
                reply_lines.append("\nReply **yes** to schedule them, or **no** to discard.")
                result = {
                    "reply": "\n".join(reply_lines),
                    "action": "study_plan_proposal",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                    "proposal": proposal,
                }
                _pending_proposals[user_id] = proposal
                return result
            except Exception as e:
                logger.warning(f"Study plan proposal failed: {e}")
                return {
                    "reply": f"I couldn't draft a plan right now ({e}).",
                    "action": "study_plan_error",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }

        # Generic scheduling query — suggest actions
        return {
            "reply": "I can help with your calendar! Try:\n- 'remind me...' to set a reminder\n- 'study plan' to generate a plan\n- 'what's on my calendar' to see events\n- 'open calendar' to launch the full calendar",
            "action": "inline_answer",
            "intent": Intent.SCHEDULING,
            "agent_id": "calendar",
        }

    async def confirm_study_plan(self, user_id: str, proposal: list[dict]) -> dict:
        """Schedule all events from an approved study-plan proposal."""
        created = []
        for p in proposal or []:
            try:
                ev = await schedule_event(
                    user_id=user_id,
                    title=p.get("title", "Study session"),
                    event_type=p.get("type", "study"),
                    date=p.get("date"),
                    time=p.get("time"),
                    duration_mins=p.get("duration_mins", 60),
                    description=p.get("description", ""),
                    ta_id=p.get("ta_id"),
                    reminder_minutes_before=p.get("reminder_minutes_before"),
                )
                created.append(ev)
            except Exception as e:
                logger.warning(f"schedule_event failed for proposal item: {e}")
        return {"scheduled": created, "count": len(created)}

    async def broker(self, env: dict) -> dict:
        # Prefer Google Calendar if connected; otherwise Lumen's internal calendar.
        gcal_r = await self.handle_google_calendar(env["user_id"], env["message"])
        if gcal_r is not None:
            return gcal_r
        return _ensure_intent(
            await a2a_tasks_send("/a2a/calendar", env["message"], env["user_id"],
                                 env.get("user_name", "")),
            Intent.SCHEDULING, "calendar",
        )


agent = CalendarAgent()
registry.register_agent(agent)

# Back-compat aliases for `from app.agents.interaction_manager import _handle_*`.
_handle_google_calendar = agent.handle_google_calendar
_handle_calendar_query = agent.handle_calendar_query
_handle_scheduling = agent.handle_scheduling
confirm_study_plan = agent.confirm_study_plan
