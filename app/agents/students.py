"""Student management — in-memory store with disk persistence.

Students are the learners whose artifacts are tracked in the portfolio.
Each student has a unique id, name, grade (class), and section.

Storage: /home/data/students.json (Azure) or ./data/students.json (local).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UTC = timezone.utc


# ── Disk storage ───────────────────────────────────────────────

def _resolve_store_path() -> Path:
    if os.path.isdir("/home"):
        return Path("/home/data/students.json")
    return Path("data/students.json")


_STORE_PATH = _resolve_store_path()


def _load() -> dict[str, dict]:
    try:
        if _STORE_PATH.exists():
            with _STORE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"Failed to load students store: {e}")
    return {}


def _flush(store: dict) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        tmp.replace(_STORE_PATH)
    except Exception as e:
        logger.warning(f"Failed to persist students store: {e}")


# In-memory store
_students: dict[str, dict] = _load()

# Seed demo students if store is empty
_SEED_STUDENTS = [
    {"name": "Priya Sharma",   "grade": "8", "section": "A"},
    {"name": "Rahul Patel",    "grade": "8", "section": "A"},
    {"name": "Aisha Khan",     "grade": "8", "section": "B"},
    {"name": "Arjun Nair",     "grade": "9", "section": "A"},
    {"name": "Meera Iyer",     "grade": "9", "section": "B"},
]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _ensure_seeds() -> None:
    if not _students:
        for seed in _SEED_STUDENTS:
            _id = str(uuid.uuid4())[:8]
            _students[_id] = {
                "id": _id,
                "name": seed["name"],
                "grade": seed["grade"],
                "section": seed["section"],
                "slug": _slugify(f"{seed['name']}-{seed['grade']}{seed['section']}"),
                "created_at": datetime.now(UTC).isoformat(),
            }
        _flush(_students)
        logger.info(f"Seeded {len(_students)} demo students")


_ensure_seeds()


# ── Public API ─────────────────────────────────────────────────

def list_students() -> list[dict]:
    """Return all students sorted by grade, section, name."""
    students = list(_students.values())
    return sorted(students, key=lambda s: (s.get("grade", ""), s.get("section", ""), s.get("name", "")))


def get_student(student_id: str) -> dict | None:
    return _students.get(student_id)


def create_student(name: str, grade: str, section: str) -> dict:
    """Create and persist a new student. Returns the created student dict."""
    student_id = str(uuid.uuid4())[:8]
    slug = _slugify(f"{name}-{grade}{section}")
    student: dict[str, Any] = {
        "id": student_id,
        "name": name.strip(),
        "grade": grade.strip(),
        "section": section.strip().upper(),
        "slug": slug,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _students[student_id] = student
    _flush(_students)
    return student


def student_folder_prefix(student_id: str) -> str | None:
    """Return the GitHub sub-folder prefix for a student, e.g. 'students/priya-sharma-8a'.
    Returns None if student not found.
    """
    student = _students.get(student_id)
    if not student:
        return None
    return f"students/{student['slug']}"
