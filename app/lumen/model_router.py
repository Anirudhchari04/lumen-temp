"""Model routing — pick cheap vs premium deployment based on message complexity.

Simple heuristic: short non-teaching messages → mini; everything else → full.
Saves ~50-75% on token cost without noticeable quality impact on greetings,
progress lookups, and routine navigation.
"""

from __future__ import annotations

from app.config import settings


TEACHING_KEYWORDS = (
    "teach", "explain", "derive", "prove", "solve", "code", "implement",
    "algorithm", "theorem", "proof", "write a program", "debug",
    "why does", "how does", "walk me through",
)


def pick_model(message: str, *, force_full: bool = False) -> str:
    """Return the deployment name to use for this message."""
    if force_full:
        return settings.azure_openai_deployment
    msg = (message or "").lower().strip()
    if len(msg) > 200:
        return settings.azure_openai_deployment
    if any(kw in msg for kw in TEACHING_KEYWORDS):
        return settings.azure_openai_deployment
    # Short & non-teaching → mini (with graceful fall-back if unset).
    return settings.azure_openai_mini_deployment or settings.azure_openai_deployment
