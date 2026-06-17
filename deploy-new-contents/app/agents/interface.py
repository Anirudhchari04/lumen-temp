"""A2A Interface Contract — Pydantic models for Lumen ↔ Orchestrator ↔ TA communication.

Any TA (mock or real) must accept TARequest and return TAResponse.
Lumen sends LumenRequest to Orchestrator, receives OrchestratorResponse.
"""

from __future__ import annotations
from pydantic import BaseModel


# ── Lumen → Orchestrator ─────────────────────────────────────

class LumenRequest(BaseModel):
    """What Lumen sends to the Orchestrator."""
    message: str
    user_id: str
    user_name: str = "Student"
    student_progress: dict = {}   # Raw progress from Lumen's DB (Lumen doesn't interpret)
    thread_id: str | None = None


# ── Orchestrator → TA ────────────────────────────────────────

class StudentContext(BaseModel):
    """Student context passed to a TA. Raw data — TA interprets it."""
    user_id: str
    name: str = "Student"
    progress: dict = {}           # Raw progress for THIS TA (from Lumen DB)
    cross_ta_progress: list[dict] = []  # Progress from other TAs
    tc_inventory: dict = {}       # {mastered: [...], in_progress: [...]}

class TARequest(BaseModel):
    """What the Orchestrator sends to a TA via A2A."""
    message: str
    student_context: StudentContext
    thread_id: str | None = None


# ── TA → Orchestrator → Lumen ────────────────────────────────

class ProgressReport(BaseModel):
    """Progress analysis produced by the TA (TA owns all interpretation)."""
    topics_covered: list[str] = []
    topics_mastered: list[str] = []
    tc_updates: dict = {}         # {"tc-id": {"status": "mastered|in_progress", "progress_pct": 80, "evidence": "..."}}
    current_level: int = 1
    current_module: str = ""
    level_label: str = "beginner"
    summary: str = ""

class TAResponse(BaseModel):
    """What a TA returns via A2A. Any TA must follow this contract."""
    reply: str
    progress_report: ProgressReport = ProgressReport()
    ta_metadata: dict | None = None  # Optional: curriculum position, next recommended


# ── Orchestrator → Lumen ─────────────────────────────────────

class OrchestratorResponse(BaseModel):
    """What the Orchestrator returns to Lumen."""
    reply: str
    ta_id: str | None = None
    ta_name: str = ""
    progress_report: ProgressReport = ProgressReport()
    routed_to: str | None = None
    intent: str = "learning"      # learning | progress | meta
