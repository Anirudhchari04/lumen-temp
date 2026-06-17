"""Wolfram Alpha agent — math / physics / computational queries.

Uses the **Full Results API v2** (structured pods), with the Short Answers API
as a fast-path when the user wants a quick answer.

Env: `WOLFRAM_APP_ID` (default already set to LQT3XP2TRY in app/config.py).

Docs: https://products.wolframalpha.com/api/documentation
"""

from __future__ import annotations

import logging
import urllib.parse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

FULL_API = "https://api.wolframalpha.com/v2/query"
SHORT_API = "https://api.wolframalpha.com/v1/result"


# ── Short Answers (fast-path) ────────────────────────────────────────────────

async def short_answer(question: str) -> str:
    """One-line plain-text answer. Returns "" if Wolfram can't answer."""
    app_id = settings.wolfram_app_id
    if not app_id:
        return ""
    params = {"i": question, "appid": app_id}
    url = f"{SHORT_API}?{urllib.parse.urlencode(params)}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers={"User-Agent": "Lumen-Bot/1.0"})
        # Short API returns 501 when it can't compute — fall back to full
        if resp.status_code == 200:
            return (resp.text or "").strip()
        return ""
    except Exception as e:
        logger.warning(f"Wolfram short_answer failed: {e}")
        return ""


# ── Full Results API v2 ─────────────────────────────────────────────────────

def _extract_pod(pod: dict) -> dict:
    """Pull title + plaintext + image from a Wolfram pod."""
    subpods = pod.get("subpods", []) or []
    texts = []
    images = []
    for sp in subpods:
        plaintext = (sp.get("plaintext") or "").strip()
        if plaintext:
            texts.append(plaintext)
        img = sp.get("img") or {}
        if img.get("src"):
            images.append({
                "src": img["src"],
                "alt": img.get("alt", ""),
                "width": img.get("width", 0),
                "height": img.get("height", 0),
            })
    return {
        "title": pod.get("title", ""),
        "scanner": pod.get("scanner", ""),  # e.g. "Result", "Plot", "Properties"
        "text": "\n".join(texts).strip(),
        "images": images,
    }


async def full_query(question: str, podstate: str | None = None) -> dict:
    """Run the Full Results API.

    Returns:
        {
          "ok": bool,
          "primary": "<one-line text answer if available>",
          "pods": [ {title, scanner, text, images}, ... ],
          "interpreted": "<how Wolfram parsed the input>",
          "error": str | None,
        }
    """
    app_id = settings.wolfram_app_id
    if not app_id:
        return {"ok": False, "error": "WOLFRAM_APP_ID not configured", "pods": [], "primary": ""}

    params = {
        "input": question,
        "appid": app_id,
        "output": "JSON",
        "format": "plaintext,image",
        # podstate lets us request "Step-by-step solution" etc.
        # If caller passes podstate, include it
    }
    if podstate:
        params["podstate"] = podstate

    url = f"{FULL_API}?{urllib.parse.urlencode(params)}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "Lumen-Bot/1.0"})
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "pods": [], "primary": ""}
        data = resp.json().get("queryresult", {}) or {}
    except Exception as e:
        logger.warning(f"Wolfram full_query failed: {e}")
        return {"ok": False, "error": str(e), "pods": [], "primary": ""}

    if not data.get("success"):
        # Common failure mode: didn't understand the query
        error = data.get("error")
        msg = error.get("msg") if isinstance(error, dict) else None
        return {
            "ok": False,
            "error": msg or "Wolfram couldn't interpret the question",
            "pods": [],
            "primary": "",
            "interpreted": "",
        }

    raw_pods = data.get("pods", []) or []
    pods = [_extract_pod(p) for p in raw_pods]

    # Primary answer — Wolfram tags the most important pod with primary=True OR id=Result
    primary_text = ""
    for p, raw in zip(pods, raw_pods):
        if raw.get("primary") or raw.get("id") in ("Result", "DecimalApproximation", "Solution"):
            primary_text = p.get("text") or primary_text
            if primary_text:
                break
    if not primary_text and pods:
        # Fall back to the first pod that has text
        for p in pods:
            if p.get("text"):
                primary_text = p["text"]
                break

    interpreted = ""
    for p, raw in zip(pods, raw_pods):
        if raw.get("id") == "Input":
            interpreted = p.get("text", "")
            break

    return {
        "ok": True,
        "primary": primary_text,
        "pods": pods,
        "interpreted": interpreted,
        "error": None,
    }


# ── Convenience: ask Wolfram and format for chat ────────────────────────────

async def ask(question: str, want_steps: bool = False) -> dict:
    """High-level entrypoint used by the chat handler.

    Tries Short Answers first (fast + cheap), falls back to Full Results when
    Short can't answer OR when `want_steps=True`.

    Returns:
        {
          "answer": str,           # plain text, ready to drop in chat
          "pods": [...],           # additional pods (Plot, Properties, etc.)
          "interpreted": str,      # how Wolfram parsed the input
          "image_url": str | None, # first plot/diagram image if any
          "source": "short" | "full",
        }
    """
    if not want_steps:
        sa = await short_answer(question)
        if sa and not sa.lower().startswith("wolfram"):
            return {
                "answer": sa, "pods": [], "interpreted": "", "image_url": None,
                "source": "short",
            }

    fr = await full_query(question, podstate="Step-by-step solution" if want_steps else None)
    if not fr.get("ok"):
        return {
            "answer": fr.get("error") or "Wolfram couldn't answer.",
            "pods": [], "interpreted": "", "image_url": None, "source": "full",
        }
    # Combine the primary answer + any step-by-step pod into the answer text
    answer_lines = []
    if fr.get("primary"):
        answer_lines.append(fr["primary"])
    for p in fr.get("pods", []):
        title = (p.get("title") or "").lower()
        if "step" in title and p.get("text"):
            answer_lines.append(f"\n**Step by step:**\n{p['text']}")
        elif "solution" in title and p.get("text") and "step" not in answer_lines[0].lower() if answer_lines else True:
            # Already covered by primary in most cases
            pass

    # Find the first plot/diagram image
    image_url = None
    for p in fr.get("pods", []):
        if p.get("images") and ("plot" in p.get("scanner", "").lower() or "plot" in p.get("title", "").lower()):
            image_url = p["images"][0].get("src")
            break

    return {
        "answer": "\n".join(answer_lines).strip() or "(no result)",
        "pods": fr.get("pods", []),
        "interpreted": fr.get("interpreted", ""),
        "image_url": image_url,
        "source": "full",
    }
