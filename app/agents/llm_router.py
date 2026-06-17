"""LLM-based intent router for Lumen.

Used as a FALLBACK only — fires when classify_intent() returns Intent.GENERAL
or when a message looks like it might contain multiple intents joined with
'and'/'then'/'also'. Returns a list so the dispatcher can fan out across
sub-agents in a single turn (e.g. "note this down and remind me Monday" →
notion + calendar).

Single LLM call per ambiguous turn. Cached by message hash for 60s to avoid
double-billing retries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import NamedTuple

from app.config import settings
from app.lumen.token_tracker import estimate_tokens, record_usage

logger = logging.getLogger(__name__)


class IntentMatch(NamedTuple):
    agent: str   # one of: notion / drive / gmail / calendar / portfolio / communication / general / shiksha
    task: str    # rephrased single-task description fed to that sub-agent
    confidence: float = 0.8


# In-process cache: hash(message + history-tail) -> (timestamp, list[IntentMatch])
_router_cache: dict[str, tuple[float, list[IntentMatch]]] = {}
_CACHE_TTL = 60.0  # seconds

# Circuit breaker — if the LLM router fails 3 times in a row, disable for 5 min
_router_failures = {"count": 0, "disabled_until": 0.0}


# ── Heuristic gate: when is LLM routing worth the call? ────────────────────

def looks_like_multi_intent(message: str) -> bool:
    """Cheap pre-check. Should we even call the LLM router?

    Returns True if the message has signals suggesting multiple distinct tasks.
    False = single-intent (or already-classified by regex); skip the LLM.
    """
    if not message:
        return False
    msg = message.lower().strip()
    if len(msg.split()) < 4:
        return False
    multi_signals = [
        " and ", " then ", " also ", " plus ", "; ",
        " after ", " before ", " followed by ", ", then ",
    ]
    return any(s in msg for s in multi_signals)


# ── System prompt for the router LLM ───────────────────────────────────────

_ROUTER_SYSTEM = """You are Lumen — the user's personal orchestrator. Decide which sub-agent(s) handle the user's message.

AVAILABLE AGENTS:
- notion: Read / create / edit / search Notion pages and databases
- drive: Google Drive ONLY — Google Docs, Sheets, PDFs in Drive. Requires an explicit Drive/Google-Docs cue ("my drive", "google doc", "a sheet"). Do NOT use for portfolio/TA folders.
- gmail: Gmail / Outlook messages — read / send / search / summarize inbox
- calendar: Calendar events — list / search / create / delete (Google, Outlook, or internal)
- communication: Compose and send emails (cross-provider — Outlook extension or Gmail)
- portfolio: GitHub portfolio — the user's learning files/artifacts organised in TA folders (math-ta, cs-ta, science-ta, english-ta, social-ta, general); list/upload/delete files, list repos & commits. ANY mention of a "<subject>-ta folder", "my portfolio", "portfolio files", or files inside a subject folder → portfolio (NOT drive).
- shiksha: Learning, TA progress, course state, session history with TAs
- arxiv: Research papers — search arXiv, fetch abstracts, summarize papers
- wolfram: Math, physics, computational questions; unit / currency / date conversions; step-by-step solutions
- social: Peer messaging, study groups, who else is learning, compare progress
- general: Casual chat, progress questions, greetings, meta questions, anything that doesn't fit above

RULES:
1. Return ONE entry per distinct task in the message.
2. Re-phrase each task as a clean single-action sentence for that agent.
3. If the message is a single task, return ONE entry.
4. If the message is purely conversational ("how are you", "thanks", "hi"), return one general entry.
5. Prefer ordered execution — earlier entries run first (use this when later steps depend on earlier ones).
6. Pick the MOST SPECIFIC agent. e.g. "find papers on RAG" → arxiv, NOT general. "what's 2 + 2" → wolfram, NOT general.
7. Progress / "how am I doing" / "my level" → general (Lumen handles progress itself).
8. Email reading/inbox ("check my email", "any new mail", "emails from X") → gmail.
9. Email composing/sending ("send a mail to X", "email X about Y") → communication.
10. "my peers" / "message X" / "study group" → social.
11. TA folders (math-ta, cs-ta, science-ta, …) and "my portfolio" ALWAYS mean portfolio (GitHub), never drive. "files in my math ta folder" → portfolio.
12. USE THE RECENT CONVERSATION to resolve vague references. If the user was just talking about their portfolio/files and then says "show me the files there" / "what's in it" / "that folder", route to the SAME agent (portfolio). Don't reset to a generic agent.

Return STRICT JSON:
{"intents":[{"agent":"<agent_id>","task":"<rephrased task>"}]}

EXAMPLES:
User: "note down call mom and remind me at 6pm"
{"intents":[{"agent":"notion","task":"create a note: call mom"},{"agent":"calendar","task":"remind me at 6pm to call mom"}]}

User: "send a mail to alice and create a doc for meeting notes"
{"intents":[{"agent":"communication","task":"send a mail to alice about meeting notes"},{"agent":"drive","task":"create a Google Doc titled meeting notes"}]}

User: "find papers on RAG and add a Notion note about each"
{"intents":[{"agent":"arxiv","task":"search arxiv for papers on RAG"},{"agent":"notion","task":"create Notion notes summarizing the top RAG papers"}]}

User: "what's the integral of sin x dx"
{"intents":[{"agent":"wolfram","task":"integrate sin(x) dx"}]}

User: "show me the boiling point of water at 5000 feet"
{"intents":[{"agent":"wolfram","task":"boiling point of water at 5000 feet"}]}

User: "find recent papers on transformers and summarize the top one"
{"intents":[{"agent":"arxiv","task":"search arxiv for recent papers on transformers"},{"agent":"arxiv","task":"summarize the top transformer paper"}]}

User: "what's in my drive"
{"intents":[{"agent":"drive","task":"list my Drive files"}]}

User: "files in my math ta folder"
{"intents":[{"agent":"portfolio","task":"list files in the math-ta folder"}]}

User: "show me my portfolio files"
{"intents":[{"agent":"portfolio","task":"list my portfolio files"}]}

RECENT CONVERSATION:
assistant: The Portfolio agent stores your files in TA folders like math-ta, cs-ta...
User: "show me the files in there"
{"intents":[{"agent":"portfolio","task":"list my portfolio files"}]}

User: "summarize my latest email from bob and add the summary to my notion page Daily Log"
{"intents":[{"agent":"gmail","task":"summarize the latest email from bob"},{"agent":"notion","task":"add the summary to my notion page Daily Log"}]}

User: "did vedanth get back to me"
{"intents":[{"agent":"gmail","task":"find recent emails from vedanth"}]}

User: "convert 14 light years to kilometres"
{"intents":[{"agent":"wolfram","task":"convert 14 light years to kilometres"}]}

User: "step by step solve x^2 - 5x + 6 = 0"
{"intents":[{"agent":"wolfram","task":"step by step solve x^2 - 5x + 6 = 0"}]}

User: "what should i work on today"
{"intents":[{"agent":"general","task":"suggest what to work on today"}]}

User: "how am i doing"
{"intents":[{"agent":"general","task":"show my progress"}]}

User: "check my email"
{"intents":[{"agent":"gmail","task":"list recent inbox emails"}]}

User: "send vedanth a mail about the project deadline"
{"intents":[{"agent":"communication","task":"compose email to vedanth about the project deadline"}]}

User: "who else is learning calculus"
{"intents":[{"agent":"social","task":"find peers studying calculus"}]}

User: "message priya about the study group"
{"intents":[{"agent":"social","task":"send a peer message to priya about the study group"}]}

User: "what's on my calendar today"
{"intents":[{"agent":"calendar","task":"list today's calendar events"}]}

User: "show my shiksha progress"
{"intents":[{"agent":"shiksha","task":"show learning progress across courses"}]}
"""


# ── Main router call ────────────────────────────────────────────────────────

async def llm_classify_multi(user_id: str, message: str,
                              history: list[dict] | None = None) -> list[IntentMatch]:
    """Ask gpt-54-mini for a list of intents. Returns [] on failure."""
    if not message:
        return []
    # Circuit breaker
    now = time.time()
    if _router_failures["disabled_until"] > now:
        return []

    # Cache key — message + the last 2 history turns (so "send it to her" is context-cached)
    history = history or []
    last_two = "|".join((h.get("content") or "")[:200] for h in history[-2:])
    key = hashlib.sha256(f"{message}::{last_two}".encode()).hexdigest()
    cached = _router_cache.get(key)
    if cached and cached[0] > now - _CACHE_TTL:
        return cached[1]

    # Build the user prompt — message + a brief history
    history_block = ""
    if history:
        recent = history[-4:]
        history_lines = []
        for h in recent:
            role = h.get("role", "user")
            content = (h.get("content") or "")[:300]
            if content:
                history_lines.append(f"{role}: {content}")
        if history_lines:
            history_block = "RECENT CONVERSATION:\n" + "\n".join(history_lines) + "\n\n"
    user_prompt = f"{history_block}USER MESSAGE: {message}\n\nReturn the JSON now."

    try:
        import asyncio

        def _call_foundry():
            from azure.ai.projects import AIProjectClient
            from azure.identity import ManagedIdentityCredential as SyncMI, DefaultAzureCredential as SyncDAC

            cred = SyncMI(client_id=settings.azure_managed_identity_client_id) if settings.azure_managed_identity_client_id else SyncDAC()
            project_client = AIProjectClient(
                credential=cred,
                endpoint=settings.foundry_endpoint or "https://anirfoundry.services.ai.azure.com",
                project_name=settings.foundry_project or "proj-anirfoundry",
            )
            oc = project_client.get_openai_client()
            return oc.responses.create(
                model="gpt-54-mini",
                instructions=_ROUTER_SYSTEM,
                input=[{"role": "user", "content": user_prompt}],
                max_output_tokens=200,
            )

        # Run the blocking SDK call off the event loop — this router now fires on
        # nearly every message, so it must not stall other concurrent requests.
        usage = None
        _t0 = time.perf_counter()
        try:
            resp = await asyncio.to_thread(_call_foundry)
            raw = (resp.output_text or "").strip()
            usage = getattr(resp, "usage", None)
        except Exception as fe:
            # Foundry path unavailable — fall back to Azure OpenAI, exactly like
            # lumen_chat does. Without this the router silently returns [] and
            # EVERY non-keyword message drops to the generic "not sure" reply.
            logger.warning(f"Router Foundry call failed, falling back to Azure OpenAI: {fe}")
            from openai import AsyncAzureOpenAI
            from azure.identity import (ManagedIdentityCredential as SyncMI,
                                        DefaultAzureCredential as SyncDAC,
                                        get_bearer_token_provider)
            cred = (SyncMI(client_id=settings.azure_managed_identity_client_id)
                    if settings.azure_managed_identity_client_id else SyncDAC())
            tp = get_bearer_token_provider(cred, "https://cognitiveservices.azure.com/.default")
            aoai = AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                azure_ad_token_provider=tp,
            )
            result = await aoai.chat.completions.create(
                model=settings.azure_openai_mini_deployment or settings.azure_openai_deployment,
                messages=[{"role": "system", "content": _ROUTER_SYSTEM},
                          {"role": "user", "content": user_prompt}],
                max_completion_tokens=200,
                temperature=0,
            )
            raw = (result.choices[0].message.content or "").strip() if result.choices else ""
            usage = getattr(result, "usage", None)

        # Record router token usage (best-effort).
        prompt_tokens = 0
        completion_tokens = 0
        if usage:
            prompt_tokens = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
        if prompt_tokens == 0 and completion_tokens == 0:
            prompt_tokens = estimate_tokens((_ROUTER_SYSTEM or "") + "\n" + user_prompt)
            completion_tokens = estimate_tokens(raw)
        if prompt_tokens or completion_tokens:
            try:
                await record_usage(user_id, prompt_tokens, completion_tokens, "gpt-54-mini",
                                   source="lumen-router", latency_ms=(time.perf_counter() - _t0) * 1000)
            except Exception:
                pass

        # Strip markdown code fences if the model added them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(raw)
        items = parsed.get("intents", [])

        # Validate + normalize entries
        VALID_AGENTS = {"notion", "drive", "gmail", "calendar", "communication",
                        "portfolio", "shiksha", "general", "arxiv", "wolfram", "social"}
        out: list[IntentMatch] = []
        for it in items[:5]:  # cap at 5 sub-tasks per turn
            agent = (it.get("agent") or "general").lower()
            if agent not in VALID_AGENTS:
                continue
            task = (it.get("task") or "").strip()
            if not task:
                continue
            out.append(IntentMatch(agent=agent, task=task))

        # Success — reset failure counter
        _router_failures["count"] = 0
        _router_cache[key] = (now, out)
        # Trim cache if too big
        if len(_router_cache) > 200:
            cutoff = now - _CACHE_TTL
            for k in list(_router_cache.keys()):
                if _router_cache[k][0] < cutoff:
                    _router_cache.pop(k, None)
        return out

    except Exception as e:
        logger.warning(f"LLM router failed: {e}")
        _router_failures["count"] += 1
        if _router_failures["count"] >= 3:
            _router_failures["disabled_until"] = now + 300  # 5-min cooldown
            logger.error("LLM router circuit breaker tripped — disabled for 5 minutes")
        return []
