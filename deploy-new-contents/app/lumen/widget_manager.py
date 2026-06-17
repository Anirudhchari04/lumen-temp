"""Widget Manager — Manages user's dashboard widgets.

Users say "add a clock" or "show a progress chart" and the UX Agent
places A2UI components in their widget zone. Layout persisted per user.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory widget store (per user) ────────────────────────
_user_widgets: dict[str, list[dict]] = {}

# Pre-defined widget templates that users can request
WIDGET_TEMPLATES: dict[str, dict] = {
    "clock": {
        "title": "Clock",
        "a2ui": {
            "surface": "widget", "root": "clock-root",
            "components": [
                {"id": "clock-root", "type": "Clock", "props": {}},
            ],
        },
    },
    "calendar": {
        "title": "My Calendar",
        "a2ui": {
            "surface": "widget", "root": "cal-root",
            "components": [
                {"id": "cal-root", "type": "Calendar", "props": {"year": 2026, "month": 4, "events": []}},
            ],
        },
    },
    "progress": {
        "title": "Progress Overview",
        "a2ui": {
            "surface": "widget", "root": "prog-root",
            "components": [
                {"id": "prog-root", "type": "Card", "props": {"variant": "outlined"}, "children": ["prog-h", "prog-bar"]},
                {"id": "prog-h", "type": "Heading", "props": {"text": "Learning Progress", "level": 4}},
                {"id": "prog-bar", "type": "ProgressBar", "props": {"label": "Overall", "value": 0}},
            ],
        },
    },
    "checklist": {
        "title": "Today's Tasks",
        "a2ui": {
            "surface": "widget", "root": "check-root",
            "components": [
                {"id": "check-root", "type": "Checklist", "props": {"title": "Today's Tasks", "items": [
                    {"label": "Review math notes", "done": False},
                    {"label": "Practice coding", "done": False},
                    {"label": "Read chapter 5", "done": False},
                ]}},
            ],
        },
    },
    "gauge": {
        "title": "Study Streak",
        "a2ui": {
            "surface": "widget", "root": "gauge-root",
            "components": [
                {"id": "gauge-root", "type": "Gauge", "props": {"value": 13, "max": 30, "label": "Sessions", "unit": "days"}},
            ],
        },
    },
    "stats": {
        "title": "Quick Stats",
        "a2ui": {
            "surface": "widget", "root": "stats-root",
            "components": [
                {"id": "stats-root", "type": "Grid", "props": {"columns": 2, "gap": "8px"}, "children": ["s1", "s2", "s3", "s4"]},
                {"id": "s1", "type": "Stat", "props": {"label": "Sessions", "value": "13", "trend": "up"}},
                {"id": "s2", "type": "Stat", "props": {"label": "Mastered", "value": "3"}},
                {"id": "s3", "type": "Stat", "props": {"label": "In Progress", "value": "1"}},
                {"id": "s4", "type": "Stat", "props": {"label": "Streak", "value": "5 days", "trend": "up"}},
            ],
        },
    },
    "map": {
        "title": "Map",
        "a2ui": {
            "surface": "widget", "root": "map-root",
            "components": [
                {"id": "map-root", "type": "Map", "props": {"lat": 12.9716, "lng": 77.5946, "zoom": 13, "title": "Bengaluru"}},
            ],
        },
    },
    "piechart": {
        "title": "Learning Distribution",
        "a2ui": {
            "surface": "widget", "root": "pie-root",
            "components": [
                {"id": "pie-root", "type": "PieChart", "props": {"title": "Time by Subject", "data": [
                    {"label": "Math", "value": 65, "tone": "success"},
                    {"label": "CS", "value": 25},
                    {"label": "Other", "value": 10, "tone": "warning"},
                ]}},
            ],
        },
    },
}

# Keywords that map to widget templates
WIDGET_KEYWORDS: dict[str, str] = {
    "clock": "clock",
    "time": "clock",
    "calendar": "calendar",
    "progress": "progress",
    "progress chart": "progress",
    "progress bar": "progress",
    "checklist": "checklist",
    "todo": "checklist",
    "tasks": "checklist",
    "to do": "checklist",
    "gauge": "gauge",
    "streak": "gauge",
    "stats": "stats",
    "statistics": "stats",
    "quick stats": "stats",
    "numbers": "stats",
    "map": "map",
    "location": "map",
    "bengaluru": "map",
    "bangalore": "map",
    "pie chart": "piechart",
    "pie": "piechart",
    "distribution": "piechart",
    "chart": "piechart",
}


def detect_widget_command(message: str) -> tuple[str, str] | None:
    """Detect if a message is a widget add/remove command.
    Returns (action, widget_key) or None.
    """
    msg = message.lower().strip()

    # "add a map of X" — special case for maps with location
    import re
    map_match = re.match(r"(?:add|show|put|place)\s+(?:a\s+)?map\s+(?:of\s+)?(.+)", msg)
    if map_match:
        return ("add", "map")

    # "add a clock" / "show a progress chart" / "put a calendar"
    # Match the widget keyword only at the START of the remainder so that messages like
    # "add june 12 as my bday in google calendar" don't get mis-detected as widget commands.
    for prefix in ["add a ", "add ", "show a ", "show ", "put a ", "put ", "place a ", "place "]:
        if msg.startswith(prefix):
            remainder = msg[len(prefix):].strip().rstrip(".")
            # Strip an extra leading article if present
            cleaned = re.sub(r"^(?:a|an|the)\s+", "", remainder, flags=re.IGNORECASE).strip()
            for kw, template_key in WIDGET_KEYWORDS.items():
                # Whole-word boundary: keyword must be the entire remainder OR
                # followed by a space + end / period (not embedded in a longer phrase)
                if cleaned == kw or cleaned.startswith(kw + " ") or cleaned == kw + " widget":
                    return ("add", template_key)

    # "remove the clock" / "delete the calendar"
    for prefix in ["remove the ", "remove ", "delete the ", "delete ", "hide the ", "hide "]:
        if msg.startswith(prefix):
            remainder = msg[len(prefix):].strip().rstrip(".")
            cleaned = re.sub(r"^(?:a|an|the)\s+", "", remainder, flags=re.IGNORECASE).strip()
            for kw, template_key in WIDGET_KEYWORDS.items():
                if cleaned == kw or cleaned.startswith(kw + " ") or cleaned == kw + " widget":
                    return ("remove", template_key)

    return None


def get_widgets(user_id: str) -> list[dict]:
    """Get all widgets for a user."""
    return _user_widgets.get(user_id, [])


def add_widget(user_id: str, template_key: str) -> dict | None:
    """Add a widget from a template. Returns the new widget or None."""
    template = WIDGET_TEMPLATES.get(template_key)
    if not template:
        return None

    import copy
    widget = {
        "id": str(uuid.uuid4())[:8],
        "template": template_key,
        "title": template["title"],
        "a2ui": copy.deepcopy(template["a2ui"]),
    }

    if user_id not in _user_widgets:
        _user_widgets[user_id] = []

    # Don't add duplicates
    existing = [w["template"] for w in _user_widgets[user_id]]
    if template_key in existing:
        return None  # Already has this widget

    _user_widgets[user_id].append(widget)
    return widget


def remove_widget(user_id: str, template_key: str) -> bool:
    """Remove a widget by template key."""
    widgets = _user_widgets.get(user_id, [])
    for i, w in enumerate(widgets):
        if w["template"] == template_key:
            widgets.pop(i)
            return True
    return False


def remove_widget_by_id(user_id: str, widget_id: str) -> bool:
    """Remove a widget by its unique ID."""
    widgets = _user_widgets.get(user_id, [])
    for i, w in enumerate(widgets):
        if w["id"] == widget_id:
            widgets.pop(i)
            return True
    return False
