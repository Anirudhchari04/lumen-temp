"""Seed demo peers — creates realistic student profiles for peer discovery demo."""

from __future__ import annotations

import logging
from datetime import datetime, timezone as _tz
UTC = _tz.utc

from app.lumen.core import get_lumen, save_lumen

logger = logging.getLogger(__name__)

DEMO_PEERS = [
    {
        "id": "peer-priya",
        "name": "Priya S.",
        "email": "priya@demo.local",
        "curriculum_progress": {
            "math-ta": {
                "ta_name": "Mathematics TA",
                "current_level": 3,
                "current_module": "Linear Algebra",
                "level_label": "intermediate",
                "session_count": 22,
                "topics_covered": ["numbers", "arithmetic", "algebra", "variables", "equations", "functions", "vectors", "matrices"],
                "topics_mastered": ["numbers", "arithmetic", "algebra", "variables", "equations"],
                "last_summary": "Working on matrix multiplication and linear transformations",
            },
            "cs-ta": {
                "ta_name": "Computer Science TA",
                "current_level": 2,
                "current_module": "Functions",
                "level_label": "beginner",
                "session_count": 8,
                "topics_covered": ["variables", "types", "control flow", "loops", "functions"],
                "topics_mastered": ["variables", "types", "control flow"],
                "last_summary": "Learning function parameters and return values",
            },
        },
        "tc_inventory": {
            "mastered": [
                {"tc_id": "math-number-sense", "evidence": "Strong arithmetic", "crossed_at": "2026-03-01", "source_ta": "math-ta"},
                {"tc_id": "math-variable-as-unknown", "evidence": "Solves multi-step equations", "crossed_at": "2026-03-15", "source_ta": "math-ta"},
                {"tc_id": "math-function-concept", "evidence": "Understands function notation", "crossed_at": "2026-04-01", "source_ta": "math-ta"},
                {"tc_id": "cs-variables-types", "evidence": "Comfortable with Python types", "crossed_at": "2026-03-20", "source_ta": "cs-ta"},
            ],
            "in_progress": [
                {"tc_id": "math-linear-transform", "progress_pct": 45},
                {"tc_id": "cs-abstraction", "progress_pct": 30},
            ],
        },
        "social": {"discoverable": True, "share_progress": True},
    },
    {
        "id": "peer-rahul",
        "name": "Rahul M.",
        "email": "rahul@demo.local",
        "curriculum_progress": {
            "math-ta": {
                "ta_name": "Mathematics TA",
                "current_level": 4,
                "current_module": "Calculus I",
                "level_label": "intermediate",
                "session_count": 35,
                "topics_covered": ["numbers", "arithmetic", "algebra", "functions", "vectors", "limits", "derivatives"],
                "topics_mastered": ["numbers", "arithmetic", "algebra", "functions", "vectors", "limits"],
                "last_summary": "Practicing chain rule and product rule",
            },
        },
        "tc_inventory": {
            "mastered": [
                {"tc_id": "math-number-sense", "evidence": "Mastered", "crossed_at": "2026-02-01", "source_ta": "math-ta"},
                {"tc_id": "math-variable-as-unknown", "evidence": "Mastered", "crossed_at": "2026-02-15", "source_ta": "math-ta"},
                {"tc_id": "math-function-concept", "evidence": "Mastered", "crossed_at": "2026-03-01", "source_ta": "math-ta"},
                {"tc_id": "math-linear-transform", "evidence": "Mastered", "crossed_at": "2026-03-20", "source_ta": "math-ta"},
                {"tc_id": "math-limits", "evidence": "Strong limit intuition", "crossed_at": "2026-04-05", "source_ta": "math-ta"},
            ],
            "in_progress": [
                {"tc_id": "math-derivatives", "progress_pct": 65},
            ],
        },
        "social": {"discoverable": True, "share_progress": True},
    },
    {
        "id": "peer-maya",
        "name": "Maya K.",
        "email": "maya@demo.local",
        "curriculum_progress": {
            "cs-ta": {
                "ta_name": "Computer Science TA",
                "current_level": 4,
                "current_module": "Algorithms",
                "level_label": "intermediate",
                "session_count": 28,
                "topics_covered": ["variables", "types", "control flow", "loops", "functions", "lists", "dicts", "sorting", "searching", "recursion"],
                "topics_mastered": ["variables", "types", "functions", "lists", "dicts", "sorting"],
                "last_summary": "Implementing recursive algorithms and analyzing Big-O",
            },
            "math-ta": {
                "ta_name": "Mathematics TA",
                "current_level": 2,
                "current_module": "Algebra",
                "level_label": "beginner",
                "session_count": 5,
                "topics_covered": ["numbers", "arithmetic", "variables"],
                "topics_mastered": ["numbers", "arithmetic"],
                "last_summary": "Starting algebraic expressions",
            },
        },
        "tc_inventory": {
            "mastered": [
                {"tc_id": "cs-variables-types", "evidence": "Mastered", "crossed_at": "2026-02-10", "source_ta": "cs-ta"},
                {"tc_id": "cs-abstraction", "evidence": "Mastered", "crossed_at": "2026-03-01", "source_ta": "cs-ta"},
                {"tc_id": "cs-data-structures", "evidence": "Mastered", "crossed_at": "2026-03-20", "source_ta": "cs-ta"},
                {"tc_id": "math-number-sense", "evidence": "Mastered", "crossed_at": "2026-03-15", "source_ta": "math-ta"},
            ],
            "in_progress": [
                {"tc_id": "cs-recursion", "progress_pct": 55},
                {"tc_id": "cs-big-o", "progress_pct": 40},
                {"tc_id": "math-variable-as-unknown", "progress_pct": 20},
            ],
        },
        "social": {"discoverable": True, "share_progress": True},
    },
    {
        "id": "peer-arjun",
        "name": "Arjun T.",
        "email": "arjun@demo.local",
        "curriculum_progress": {
            "math-ta": {
                "ta_name": "Mathematics TA",
                "current_level": 1,
                "current_module": "Foundations",
                "level_label": "beginner",
                "session_count": 3,
                "topics_covered": ["numbers", "arithmetic"],
                "topics_mastered": ["numbers"],
                "last_summary": "Practicing basic arithmetic",
            },
            "cs-ta": {
                "ta_name": "Computer Science TA",
                "current_level": 1,
                "current_module": "Basics",
                "level_label": "beginner",
                "session_count": 2,
                "topics_covered": ["variables"],
                "topics_mastered": [],
                "last_summary": "Just started learning Python variables",
            },
        },
        "tc_inventory": {
            "mastered": [],
            "in_progress": [
                {"tc_id": "math-number-sense", "progress_pct": 35},
                {"tc_id": "cs-variables-types", "progress_pct": 10},
            ],
        },
        "social": {"discoverable": True, "share_progress": True},
    },
]


async def seed_demo_peers():
    """Seed demo peer lumens if they don't exist."""
    seeded = 0
    for peer in DEMO_PEERS:
        existing = await get_lumen(peer["id"])
        if existing:
            continue

        now = datetime.now(UTC).isoformat()
        lumen = {
            "id": peer["id"],
            "lumen_id": f"lumen://default/{peer['id']}",
            "name": peer["name"],
            "email": peer["email"],
            "org": "demo",
            "bio": "",
            "expertise": "",
            "interests": "",
            "preferences": {"language": "English", "pace": "moderate", "explanation": "detailed"},
            "curriculum_progress": peer["curriculum_progress"],
            "tc_inventory": peer["tc_inventory"],
            "session_history": [],
            "artifacts": [],
            "social": peer.get("social", {"discoverable": True, "share_progress": True}),
            "created_at": now,
            "updated_at": now,
        }
        await save_lumen(lumen)
        seeded += 1
        logger.info(f"Seeded demo peer: {peer['name']} ({peer['id']})")

    if seeded:
        logger.info(f"Seeded {seeded} demo peers")
    return seeded
