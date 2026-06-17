"""Lumen Agent — Personal learning companion.

Lumen NEVER teaches. It only:
1. Shows progress (concise, from DB)
2. Redirects to TAs (context switch)
3. Answers meta questions about available TAs
"""

from __future__ import annotations

import json
import logging
import time

from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
try:
    from agent_framework import Agent
except ImportError:
    from agent_framework._agents import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from app.config import settings
from app.lumen.core import get_lumen
from app.orchestrator.registry import get_all_agents, detect_ta, get_agent_card

logger = logging.getLogger(__name__)

TA_URLS: dict[str, str] = {}

_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        if settings.azure_managed_identity_client_id:
            _credential = ManagedIdentityCredential(client_id=settings.azure_managed_identity_client_id)
        else:
            _credential = DefaultAzureCredential()
    return _credential


def _get_client(model: str | None = None):
    return OpenAIChatCompletionClient(
        model=model or settings.azure_openai_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        credential=_get_credential(),
        api_version=settings.azure_openai_api_version,
    )


def _detect_context_switch(message: str) -> str | None:
    """Broadly detect if user wants to learn/study/practice/continue anything."""
    learning_phrases = [
        "teach", "learn", "explain", "help me", "study", "practice",
        "understand", "start", "continue", "begin", "open",
        "go to", "switch to", "lets", "let's",
    ]
    msg_lower = message.lower().strip()

    # Try to detect a specific registered external TA
    ta_id = detect_ta(message)
    if ta_id and ta_id.startswith("course-"):
        return ta_id

    for phrase in learning_phrases:
        if phrase in msg_lower:
            ta_id = detect_ta(message)
            if ta_id and ta_id.startswith("course-"):
                return ta_id

    return None


def _is_progress_query(message: str) -> bool:
    msg_lower = message.lower()
    keywords = [
        "progress", "how am i", "my status", "what have i learned",
        "my level", "threshold", "my score", "how far",
        "across courses", "where am i", "what's next", "whats next",
        "recommend", "what should", "doing",
    ]
    return any(kw in msg_lower for kw in keywords)


async def _build_progress_summary(user_id: str) -> str:
    """Build concise progress for LLM context — pulled live from Shiksha."""
    from app.agents import shiksha_agent

    # Fetch live progress directly from Shiksha backend
    shiksha_courses = await shiksha_agent.get_user_progress(user_id)

    if not shiksha_courses:
        return "No Shiksha course progress yet."

    summary = {}
    for item in shiksha_courses:
        aid = item.get("agent_id", "")
        summary[aid] = {
            "ta_name": item.get("name", aid),
            "sessions": item.get("thread_count", 0),
            "last_active": item.get("last_active", ""),
            "continue_url": item.get("continue_url", ""),
        }

    return json.dumps({"courses": summary}, indent=2)


from app.agents.prompt_kit import build_agent_prompt

LUMEN_PROMPT = build_agent_prompt(
    preamble=(
        "You are Lumen, the student's warm, concise personal learning companion and the "
        "orchestrator of their agent network. You track progress and point the student to "
        "the right place — you never teach academic content yourself."
    ),
    role="Lumen companion (progress + redirection)",
    mission=(
        "Answer the student's everyday questions about their learning progress, schedule, peers, "
        "and tasks, and redirect them to their Shiksha courses for any actual teaching."
    ),
    capabilities=[
        "Report Shiksha course progress concisely when asked.",
        "Redirect academic/learning requests to the student's Shiksha courses.",
        "Point the student toward schedule, peers, or email actions.",
    ],
    rules=[
        "NEVER teach or explain academic content. NEVER.",
        "Keep responses SHORT (1-3 lines max).",
        "Do NOT use markdown bold (**text**), asterisks, hashtags, or any markdown — plain text only.",
        "If the message is unclear, vague, or off-topic, reply briefly: \"Not sure what you mean — "
        "you can ask me about your progress, schedule, peers, or tell me to send an email.\"",
        "Do NOT surface progress data unless the user specifically asks about progress/learning/what's next.",
        "Only show progress for Shiksha courses. Do NOT mention Math TA or CS TA.",
        "If the user asks anything academic, say \"Check your Shiksha courses for that!\"",
        "If no Shiksha course progress exists, say \"No Shiksha course progress yet — enroll in a course to get started!\"",
    ],
    output_format="Plain text, 1-3 lines, warm and concise. No markdown.",
)


async def lumen_chat(user_id: str, message: str,
                     conversation_history: list[dict] | None = None) -> dict:
    """Lumen: progress check or context switch. Never teaches.

    conversation_history: list of {role: 'user'|'assistant', content: str}
    representing the last N messages in the current thread.
    """

    # ── Token-saving fast-paths (no LLM call) ────────────────────────────────
    # Cheap canned responses for common deterministic intents. Saves ~1k tokens
    # per call and gives an instant reply.
    import re as _re_fp
    msg_lower = (message or "").lower().strip()

    # Capabilities / "what can you do"
    cap_patterns = [
        r"\b(what\s+can\s+you\s+do|what\s+do\s+you\s+do|your\s+capabilit(?:ies|y)|"
        r"what\s+are\s+you\s+capable\s+of|what\s+do\s+you\s+know|"
        r"how\s+can\s+you\s+help|what\s+(?:all\s+)?(?:can|do)\s+you\s+offer|"
        r"list\s+(?:your\s+)?features|show\s+me\s+(?:your\s+)?features)\b",
    ]
    if any(_re_fp.search(p, msg_lower) for p in cap_patterns):
        from app.lumen.features import render_capabilities
        return {"reply": render_capabilities(), "action": "chat", "agent_id": "lumen",
                "_token_usage": {"prompt": 0, "completion": 0, "total": 0, "model": "(canned)"}}

    # "What's new" / changelog
    new_patterns = [
        r"\b(what'?s?\s+new|latest\s+(?:updates?|features?|changes?)|recent\s+(?:updates?|features?|changes?)|"
        r"any\s+(?:new\s+)?(?:features?|updates?)|changelog|release\s+notes?)\b",
    ]
    if any(_re_fp.search(p, msg_lower) for p in new_patterns):
        from app.lumen.features import render_whats_new
        return {"reply": render_whats_new(days=5), "action": "chat", "agent_id": "lumen",
                "_token_usage": {"prompt": 0, "completion": 0, "total": 0, "model": "(canned)"}}

    # Hi / hello / greeting — pick a fresh, time-aware line each time so it
    # never feels canned. Still zero-LLM (random choice over a curated set).
    if _re_fp.match(r"^(hi|hello|hey|yo|sup|hola|namaste|good\s+(morning|afternoon|evening))[\s!,.?]*$", msg_lower):
        import random as _rnd
        from datetime import datetime as _dt
        l = await get_lumen(user_id)
        name = (l or {}).get("name", "") if l else ""
        first_name = name.split()[0] if name.strip() else "there"
        _hour = _dt.now().hour
        _tod = "morning" if _hour < 12 else "afternoon" if _hour < 17 else "evening"
        _greetings = [
            f"Hey {first_name} 👋 What's on your mind?",
            f"Hi {first_name}! Want to check your progress, schedule, or fire off an email?",
            f"Good {_tod}, {first_name} ☀️ How can I help?",
            f"Hey {first_name} — ready when you are. Progress, peers, calendar, or email?",
            f"👋 Hi {first_name}! Ask me anything, or say 'open math TA' to start learning.",
            f"Yo {first_name}! What can I do for you today?",
            f"Hello {first_name} 🌿 Need your schedule, your peers, or a quick email drafted?",
            f"Hey there {first_name}! Catching up on progress or planning your day?",
        ]
        return {"reply": _rnd.choice(_greetings),
                "action": "chat", "agent_id": "lumen",
                "_token_usage": {"prompt": 0, "completion": 0, "total": 0, "model": "(canned)"}}

    # Context switch — any learning intent redirects to TA
    ta_id = _detect_context_switch(message)
    if ta_id and not _is_progress_query(message):
        card = get_agent_card(ta_id) if ta_id else None
        ta_name = card.name if card else "Teaching Assistant"
        ta_url = TA_URLS.get(ta_id, "/ta/math")
        return {
            "reply": f"Opening {ta_name} for you! Your progress syncs back automatically.",
            "action": "external_launch",
            "redirect_url": ta_url,
            "ta_id": ta_id,
        }

    # Get full user profile for rich context
    lumen = await get_lumen(user_id)
    name = lumen.get("name", "there") if lumen else "there"
    bio = (lumen or {}).get("bio", "")
    interests = (lumen or {}).get("interests", "")
    occupation = (lumen or {}).get("occupation", "")

    # Get UX preset instructions
    from app.lumen.ux_agent import get_ux_preset, get_prompt_instructions
    ux_preset = await get_ux_preset(user_id)
    ux_instructions = get_prompt_instructions(ux_preset)

    # Build user context section
    user_ctx_parts = [f"User's name: {name}"]
    if occupation: user_ctx_parts.append(f"Occupation: {occupation}")
    if interests: user_ctx_parts.append(f"Interests: {interests}")
    if bio: user_ctx_parts.append(f"About: {bio}")
    user_ctx = "\n".join(user_ctx_parts)

    if _is_progress_query(message):
        progress = await _build_progress_summary(user_id)
        prompt = (
            f"{LUMEN_PROMPT}\n\nUX STYLE: {ux_instructions}\n\n"
            f"USER PROFILE:\n{user_ctx}\n\nProgress data:\n{progress}"
        )
    else:
        prompt = (
            f"{LUMEN_PROMPT}\n\nUX STYLE: {ux_instructions}\n\n"
            f"USER PROFILE:\n{user_ctx}\n\n"
            f"The user has NOT asked about progress or learning. Do NOT mention progress, "
            f"levels, sessions, or redirect them to any TA unless they ask about learning."
        )

    from app.lumen.model_router import pick_model
    from openai import AsyncAzureOpenAI
    from azure.identity import ManagedIdentityCredential as SyncMI, DefaultAzureCredential as SyncDAC, get_bearer_token_provider

    # Build messages list
    messages_list = [{"role": "system", "content": prompt}]
    if conversation_history:
        for h in conversation_history[-20:]:
            role = h.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            content = h.get("content", "")
            if content:
                messages_list.append({"role": role, "content": content})
    messages_list.append({"role": "user", "content": message})

    # Token-usage capture
    from app.lumen.token_tracker import record_usage as _record_usage, estimate_tokens
    prompt_tokens = 0
    completion_tokens = 0
    model_used = ""
    _t0 = time.perf_counter()
    try:
        # Prefer Foundry gpt-54-mini when available
        from azure.ai.projects import AIProjectClient

        mi_cred = SyncMI(client_id=settings.azure_managed_identity_client_id) if settings.azure_managed_identity_client_id else SyncDAC()
        project_client = AIProjectClient(
            credential=mi_cred,
            endpoint=settings.foundry_endpoint or "https://anirfoundry.services.ai.azure.com",
            project_name=settings.foundry_project or "proj-anirfoundry",
        )
        oc = project_client.get_openai_client()

        # Drop the system message from `input` — we pass it via `instructions`.
        # This avoids double-billing the same prompt tokens.
        inputs = [m for m in messages_list if m["role"] != "system"]
        resp = oc.responses.create(
            model="gpt-54-mini",
            instructions=prompt,
            input=inputs,
            max_output_tokens=300,
        )
        try:
            reply = resp.output_text or ""
        except (IndexError, AttributeError):
            reply = ""
        usage = getattr(resp, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
        model_used = "gpt-54-mini"
    except Exception as e:
        logger.warning(f"Foundry gpt-54-mini failed, falling back to Azure OpenAI: {e}")
        try:
            # Azure OpenAI fallback — use UAMI with azure_ad_token_provider (not api_key)
            sync_cred = SyncMI(client_id=settings.azure_managed_identity_client_id) if settings.azure_managed_identity_client_id else SyncDAC()
            token_provider = get_bearer_token_provider(sync_cred, "https://cognitiveservices.azure.com/.default")
            aoai = AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                azure_ad_token_provider=token_provider,
            )
            chosen_model = pick_model(message)
            result = await aoai.chat.completions.create(
                model=chosen_model,
                messages=messages_list,
                max_completion_tokens=300,
                temperature=0.7,
            )
            reply = (result.choices[0].message.content or "") if result.choices else ""
            usage = getattr(result, "usage", None)
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            model_used = chosen_model
        except Exception as e2:
            logger.error(f"Lumen fallback error: {e2}")
            reply = "I'm having trouble right now. Try again!"

    # Strip any markdown that slips through
    reply = reply.replace("**", "").replace("##", "").replace("# ", "")

    # If the API didn't report usage (some Foundry/responses paths don't),
    # fall back to a tiktoken/char-based estimate so the tracker isn't stuck at 0.
    if prompt_tokens == 0 and completion_tokens == 0 and reply:
        prompt_tokens = estimate_tokens(prompt + (message or ""))
        completion_tokens = estimate_tokens(reply)
        if not model_used:
            model_used = "gpt-54-mini (estimated)"
        else:
            model_used = model_used + " (estimated)"

    # Persist token usage (best-effort, doesn't block response)
    _latency_ms = (time.perf_counter() - _t0) * 1000
    if prompt_tokens or completion_tokens:
        try:
            await _record_usage(user_id, prompt_tokens, completion_tokens, model_used,
                                source="lumen-chat", latency_ms=_latency_ms)
            logger.info(f"[lumen tokens] user={user_id} prompt={prompt_tokens} completion={completion_tokens} model={model_used} latency={_latency_ms:.0f}ms")
        except Exception as _e:
            logger.debug(f"token usage record failed: {_e}")

    return {
        "reply": reply,
        "action": "chat",
        "agent_id": "lumen",
        "_token_usage": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
            "model": model_used,
        },
    }
