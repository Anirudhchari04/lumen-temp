"""Coding TA — a demo subject agent that generates learning artifacts
(code, quizzes, notes, exercises) and silently saves each one to the student's
GitHub portfolio, organized by artifact type and date.

The TA itself never deals with GitHub: `generate_and_save` wires generation to
the portfolio auto-save so artifacts land in the linked repo as they're created,
under `coding-ta/<type>/<YYYY-MM-DD>_<title>.<ext>`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from app.config import settings
from app.agents.portfolio_agent import save_typed_artifact

logger = logging.getLogger(__name__)

TA_FOLDER = "coding-ta"

# Supported artifact types and their default file extension.
ARTIFACT_TYPES: dict[str, str] = {
    "code": "py",
    "quiz": "md",
    "notes": "md",
    "exercise": "md",
}

# Per-type output ceiling. coding-ta runs on the premium model where output
# tokens dominate cost, so cap each type to roughly what it needs rather than a
# flat 4000. Code can be a full runnable file; quizzes/notes/exercises are
# short Markdown and were over-provisioned. Tune here to trade cost vs. length.
_MAX_TOKENS: dict[str, int] = {
    "code": 4000,
    "quiz": 2000,
    "notes": 2000,
    "exercise": 2200,
}

# language -> source extension for code artifacts
_CODE_EXT = {
    "python": "py", "py": "py",
    "javascript": "js", "js": "js", "node": "js",
    "typescript": "ts", "ts": "ts",
    "java": "java", "c": "c", "c++": "cpp", "cpp": "cpp",
    "c#": "cs", "csharp": "cs", "go": "go", "golang": "go",
    "rust": "rs", "ruby": "rb", "php": "php", "swift": "swift",
    "kotlin": "kt", "sql": "sql", "bash": "sh", "shell": "sh",
    "html": "html", "css": "css",
}

_SYSTEM = """You are the Coding TA, a friendly programming teaching assistant.
The student asks for ONE learning artifact. Produce exactly that artifact.

Return ONLY a JSON object (no markdown fences, no prose) with these fields:
  "title":    short human title, max 8 words
  "language": programming language for code, lowercase (e.g. "python"); "" if not code
  "ext":      file extension WITHOUT the dot (e.g. "py", "md")
  "content":  the full artifact as plain text

Rules by type:
- code:     "content" is a complete, runnable, well-commented source file; ext matches the language.
- quiz:     "content" is Markdown with 5 questions and an answer key at the end; ext "md".
- notes:    "content" is concise Markdown study notes with examples; ext "md".
- exercise: "content" is Markdown with 3 practice problems including hints and solutions; ext "md".
Keep content focused and under ~400 lines."""


def detect_artifact_type(prompt: str) -> str:
    """Best-effort guess of artifact type from the request wording."""
    p = (prompt or "").lower()
    if any(w in p for w in ("quiz", "mcq", "multiple choice", "test me", "questions")):
        return "quiz"
    if any(w in p for w in ("exercise", "practice", "problem", "challenge", "assignment", "homework")):
        return "exercise"
    if any(w in p for w in ("notes", "explain", "summary", "summarize", "cheat sheet", "concept")):
        return "notes"
    return "code"


def _fallback_title(prompt: str) -> str:
    words = re.sub(r"[^\w\s-]", "", prompt or "").split()
    return " ".join(words[:8]) or "Untitled artifact"


def _parse_json(raw: str) -> dict:
    """Leniently parse a JSON object out of an LLM response (handles fences)."""
    if not raw:
        return {}
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            return {}
    return {}


def _aoai_client():
    """Azure OpenAI client using managed-identity token (no API key)."""
    from openai import AsyncAzureOpenAI
    from azure.identity import (ManagedIdentityCredential as SyncMI,
                                DefaultAzureCredential as SyncDAC,
                                get_bearer_token_provider)
    cred = (SyncMI(client_id=settings.azure_managed_identity_client_id)
            if settings.azure_managed_identity_client_id else SyncDAC())
    tp = get_bearer_token_provider(cred, "https://cognitiveservices.azure.com/.default")
    return AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        azure_ad_token_provider=tp,
    )


async def generate_artifact(prompt: str, artifact_type: str) -> dict:
    """Generate a single artifact via the LLM.
    Returns {title, type, language, ext, content}."""
    artifact_type = artifact_type if artifact_type in ARTIFACT_TYPES else "code"
    user_msg = (f"Artifact type: {artifact_type}\n"
                f"Student request: {prompt}\n\nReturn the JSON now.")

    client = _aoai_client()
    t0 = time.perf_counter()
    result = await client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": user_msg}],
        max_completion_tokens=_MAX_TOKENS.get(artifact_type, 4000),
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    raw = (result.choices[0].message.content or "").strip() if result.choices else ""
    data = _parse_json(raw)

    usage = getattr(result, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0 if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0 if usage else 0

    title = (data.get("title") or _fallback_title(prompt)).strip()
    content = data.get("content") or raw or ""
    language = (data.get("language") or "").strip().lower()
    ext = (data.get("ext") or "").strip().lstrip(".").lower()
    if not ext:
        if artifact_type == "code":
            ext = _CODE_EXT.get(language, "txt")
        else:
            ext = ARTIFACT_TYPES.get(artifact_type, "txt")

    # Fall back to an estimate if the API didn't report usage.
    if prompt_tokens == 0 and completion_tokens == 0:
        from app.lumen.token_tracker import estimate_tokens
        prompt_tokens = estimate_tokens(_SYSTEM + "\n" + user_msg)
        completion_tokens = estimate_tokens(raw)

    return {
        "title": title, "type": artifact_type,
        "language": language, "ext": ext, "content": content,
        "_prompt_tokens": prompt_tokens,
        "_completion_tokens": completion_tokens,
        "_latency_ms": latency_ms,
    }


async def generate_and_save(user_id: str, prompt: str,
                            artifact_type: Optional[str] = None) -> dict:
    """Generate an artifact and silently auto-save it to the linked portfolio."""
    atype = (artifact_type or "").lower().strip()
    if atype not in ARTIFACT_TYPES:
        atype = detect_artifact_type(prompt)

    try:
        art = await generate_artifact(prompt, atype)
    except Exception as e:
        logger.error(f"Coding TA generation failed: {e}")
        return {"ok": False, "error": f"Generation failed: {e}"}

    # Track token usage + latency for the cost/usage dashboard.
    try:
        from app.lumen.token_tracker import record_usage
        await record_usage(
            user_id,
            art.get("_prompt_tokens", 0),
            art.get("_completion_tokens", 0),
            model=settings.azure_openai_deployment,
            source="coding-ta",
            latency_ms=art.get("_latency_ms"),
        )
    except Exception:
        pass

    content_bytes = (art["content"] or "").encode("utf-8")
    save = await save_typed_artifact(
        user_id=user_id,
        title=art["title"],
        content_bytes=content_bytes,
        artifact_type=art["type"],
        ext=art["ext"],
        ta_folder=TA_FOLDER,
    )

    return {
        "ok": True,
        "title": art["title"],
        "type": art["type"],
        "language": art["language"],
        "ext": art["ext"],
        "content": art["content"],
        "saved": bool(save.get("ok")),
        "path": save.get("path"),
        "url": save.get("url"),
        "commit_url": save.get("commit_url"),
        "save_error": None if save.get("ok") else save.get("error"),
    }
