"""UX Agent — Personal UX preference engine.

Stores and serves tested UX presets. All agents query this before
generating responses to adapt output format, verbosity, and modality.

Per Manohar: "My personal UX agent, which I will train with my preferences
for how I want to consume anything that comes back from any of my agents."

Presets are tested and curated (not on-demand LLM generation).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Preset definitions ───────────────────────────────────────

PRESETS: dict[str, dict[str, Any]] = {
    "standard": {
        "id": "standard",
        "name": "Standard",
        "description": "Default beige UI, text responses with inline cards. Warm and concise.",
        "icon": "✦",
        "prompt_instructions": (
            "Respond in a warm, concise style. Use 2-4 lines. "
            "You may use plain text formatting. Keep it friendly."
        ),
        "tts_enabled": False,
        "stt_enabled": False,
        "font_scale": 1.0,
        "high_contrast": False,
        "verbosity": "concise",
    },
    "vision": {
        "id": "vision",
        "name": "Vision Accessible",
        "description": "Large fonts, high contrast, screen-reader friendly. Voice input and output enabled.",
        "icon": "👁️",
        "prompt_instructions": (
            "The user has visual accessibility needs. "
            "Respond in plain text only — no emojis, no markdown bold, no special symbols. "
            "Use simple sentence structure. Be descriptive but concise. "
            "Avoid references to visual elements. Structure responses as numbered lists when possible."
        ),
        "tts_enabled": True,
        "stt_enabled": True,
        "font_scale": 1.5,
        "high_contrast": True,
        "verbosity": "structured",
    },
    "audio-first": {
        "id": "audio-first",
        "name": "Audio First",
        "description": "Responses read aloud automatically. Minimal text on screen.",
        "icon": "🎧",
        "prompt_instructions": (
            "The user prefers audio output. Keep responses short and conversational — "
            "they will be read aloud. Avoid lists, tables, or visual formatting. "
            "Speak naturally as if in a conversation."
        ),
        "tts_enabled": True,
        "stt_enabled": True,
        "font_scale": 1.0,
        "high_contrast": False,
        "verbosity": "conversational",
    },
    "data-focused": {
        "id": "data-focused",
        "name": "Data Focused",
        "description": "Tables, numbers, and queryable format. No prose, just facts.",
        "icon": "📊",
        "prompt_instructions": (
            "The user wants data, not prose. Respond with structured facts: "
            "use numbers, percentages, counts. Present as bullet points or short labeled fields. "
            "No motivational text, no filler. Just the data."
        ),
        "tts_enabled": False,
        "stt_enabled": False,
        "font_scale": 1.0,
        "high_contrast": False,
        "verbosity": "data",
    },
    "minimal": {
        "id": "minimal",
        "name": "Minimal",
        "description": "Ultra-short responses. Bullet points only, no prose.",
        "icon": "⚡",
        "prompt_instructions": (
            "Be extremely brief. 1-2 lines max. Bullet points preferred. "
            "No greetings, no filler, no explanations unless asked. Just the answer."
        ),
        "tts_enabled": False,
        "stt_enabled": False,
        "font_scale": 0.95,
        "high_contrast": False,
        "verbosity": "minimal",
    },
}

DEFAULT_PRESET = "standard"


# ── Get / set preferences ────────────────────────────────────

async def get_ux_preset(user_id: str) -> dict:
    """Get the active UX preset for a user. Returns full preset dict."""
    from app.lumen.core import get_lumen
    lumen = await get_lumen(user_id)
    preset_id = DEFAULT_PRESET
    if lumen:
        preset_id = lumen.get("preferences", {}).get("ux_preset", DEFAULT_PRESET)
    return PRESETS.get(preset_id, PRESETS[DEFAULT_PRESET])


async def set_ux_preset(user_id: str, preset_id: str) -> dict:
    """Set the UX preset for a user. Returns the new preset dict."""
    if preset_id not in PRESETS:
        raise ValueError(f"Unknown preset '{preset_id}'. Available: {list(PRESETS.keys())}")

    from app.lumen.core import get_lumen, save_lumen
    lumen = await get_lumen(user_id)
    if lumen:
        prefs = lumen.get("preferences", {})
        prefs["ux_preset"] = preset_id
        lumen["preferences"] = prefs
        await save_lumen(lumen)
        logger.info(f"UX preset for {user_id} set to '{preset_id}'")

    return PRESETS[preset_id]


def get_all_presets() -> list[dict]:
    """Return all available presets (for the UI selector)."""
    return [
        {"id": p["id"], "name": p["name"], "description": p["description"], "icon": p["icon"]}
        for p in PRESETS.values()
    ]


def get_prompt_instructions(preset: dict) -> str:
    """Extract the prompt instruction string from a preset dict."""
    return preset.get("prompt_instructions", PRESETS[DEFAULT_PRESET]["prompt_instructions"])


# ── Chat-based preset switching ──────────────────────────────

SWITCH_KEYWORDS: dict[str, str] = {
    "standard mode": "standard",
    "default mode": "standard",
    "normal mode": "standard",
    "vision mode": "vision",
    "accessible mode": "vision",
    "screen reader": "vision",
    "accessibility": "vision",
    "audio mode": "audio-first",
    "audio first": "audio-first",
    "listen mode": "audio-first",
    "data mode": "data-focused",
    "data focused": "data-focused",
    "numbers mode": "data-focused",
    "minimal mode": "minimal",
    "brief mode": "minimal",
    "concise mode": "minimal",
}


def detect_preset_switch(message: str) -> str | None:
    """Detect if a message is a UX preset switch request.
    Returns preset_id or None."""
    msg = message.lower().strip()
    # "switch to X mode" / "use X mode" / "set X mode"
    for kw, preset_id in SWITCH_KEYWORDS.items():
        if kw in msg:
            return preset_id
    return None
