"""Dummy Data — Stubs for Six/Shiksha integration.

Replace these with real data from Six/Shiksha platform.
Interfaces stay the same — just swap the data source.
"""

DUMMY_MANAGER = {
    "id": "demo-manager",
    "name": "Demo Course Manager",
    "courses": [
        {"id": "math-101", "name": "Mathematics 101", "ta_id": "math-ta"},
        {"id": "cs-101", "name": "Computer Science 101", "ta_id": "cs-ta"},
    ],
}

DUMMY_PROGRESS = {
    "math-ta": {
        "current_level": 1,
        "current_module": "Foundations",
        "topics_covered": [],
        "topics_mastered": [],
        "session_count": 0,
        "level_label": "beginner",
        "last_summary": "",
    },
    "cs-ta": {
        "current_level": 1,
        "current_module": "Basics",
        "topics_covered": [],
        "topics_mastered": [],
        "session_count": 0,
        "level_label": "beginner",
        "last_summary": "",
    },
}
