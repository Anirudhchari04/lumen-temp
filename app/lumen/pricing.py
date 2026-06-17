"""Token → cost pricing for Lumen usage reporting.

The persisted per-source usage counters (see token_tracker) store prompt /
completion token splits but NOT the model, so we estimate cost by mapping each
tracking *source* to the model it predominantly uses. Good enough to compare
relative agent cost and spot optimisation targets — not an invoice.

Rates are USD per 1,000,000 tokens (input, output). The gpt-5.4 / gpt-54-mini
deployment names are internal; the numbers below mirror current Azure OpenAI
list pricing for the comparable full / mini tiers. Override with the
LUMEN_PRICING env var (JSON: {"model": [in_per_m, out_per_m]}) when you have
contracted rates.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# USD per 1,000,000 tokens: (input, output)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4": (1.25, 10.00),       # full / premium deployment
    "gpt-54-mini": (0.15, 0.60),    # mini / cheap deployment
    "_default": (0.15, 0.60),       # unknown model → assume mini
}

# Optional env override so prod can plug in real contracted rates without a deploy.
try:
    _override = os.getenv("LUMEN_PRICING")
    if _override:
        for _model, _rate in json.loads(_override).items():
            if isinstance(_rate, (list, tuple)) and len(_rate) == 2:
                MODEL_PRICING[_model] = (float(_rate[0]), float(_rate[1]))
except Exception as e:  # pragma: no cover - defensive
    logger.warning(f"Ignoring malformed LUMEN_PRICING: {e}")

# Which model each tracking source predominantly bills against.
# lumen-chat can escalate to the full model on teaching prompts (model_router),
# so it's priced at the premium tier; everything else defaults to mini.
SOURCE_MODEL: dict[str, str] = {
    "lumen-chat": "gpt-5.4",
    "lumen": "gpt-5.4",
    "lumen-router": "gpt-54-mini",
    "shiksha": "gpt-54-mini",
    # agents below run through the agent framework, predominantly on mini
    "notion": "gpt-54-mini",
    "drive": "gpt-54-mini",
    "gmail": "gpt-54-mini",
    "communication": "gpt-54-mini",
    "calendar": "gpt-54-mini",
    "arxiv": "gpt-54-mini",
    "wolfram": "gpt-54-mini",
    "social": "gpt-54-mini",
    "portfolio": "gpt-54-mini",
    "coding-ta": "gpt-5.4",
}


def rate_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD per 1M tokens for a model name."""
    if not model:
        return MODEL_PRICING["_default"]
    # Strip the "(estimated)" suffix the tracker sometimes appends.
    base = model.split(" ")[0].strip()
    return MODEL_PRICING.get(base, MODEL_PRICING["_default"])


def model_for_source(source: str) -> str:
    return SOURCE_MODEL.get((source or "").strip().lower(), "gpt-54-mini")


def cost_usd(prompt_tokens: int, completion_tokens: int,
             model: str = "", source: str = "") -> float:
    """Estimate USD cost for one (or aggregated) call.

    Pass `model` when known; otherwise pass `source` and we infer the model.
    """
    if not model and source:
        model = model_for_source(source)
    in_rate, out_rate = rate_for(model)
    return ((prompt_tokens or 0) / 1_000_000.0) * in_rate + \
           ((completion_tokens or 0) / 1_000_000.0) * out_rate


def _counter_cost(counter: dict, source: str = "") -> float:
    if not isinstance(counter, dict):
        return 0.0
    return round(cost_usd(counter.get("prompt", 0), counter.get("completion", 0),
                          source=source), 6)


def annotate_usage(usage: dict) -> dict:
    """Add `cost_usd` to a get_full_usage() result, in place, and return it.

    Adds a per-source `cost_usd` inside each *_by_source counter, plus
    top-level `cost` totals for today / week / lifetime / session.
    """
    if not isinstance(usage, dict):
        return usage

    def _annotate_source_map(source_map: dict) -> float:
        total = 0.0
        if not isinstance(source_map, dict):
            return total
        for src, cnt in source_map.items():
            if isinstance(cnt, dict):
                c = _counter_cost(cnt, src)
                cnt["cost_usd"] = c
                total += c
        return total

    cost = {}
    for window in ("today_by_source", "week_by_source", "lifetime_by_source", "session_by_source"):
        cost[window.replace("_by_source", "")] = round(_annotate_source_map(usage.get(window) or {}), 6)

    usage["cost"] = cost
    return usage


def aggregate_cost(by_source: dict) -> float:
    """Total USD across a {source: counter} map (used by the admin all-users view)."""
    total = 0.0
    if isinstance(by_source, dict):
        for src, cnt in by_source.items():
            total += _counter_cost(cnt, src)
    return round(total, 6)


# ── Advisory cost-reduction recommendations ──────────────────────────────────
# Maps each premium model to a cheaper alternative plus a candid note on where
# the cheaper tier matches it and where you should stay on premium. This is
# advice only — Lumen never switches models for you.
MODEL_ALTERNATIVES: dict[str, dict] = {
    "gpt-5.4": {
        "cheaper": "gpt-54-mini",
        "matches_on": "routing, extraction, summarization, classification and short replies",
        "keep_premium_for": "multi-step reasoning, code generation and teaching explanations",
    },
}

# Per-source guidance: is it safe to downshift this agent wholesale, or should
# only the simple turns move? Sources not listed are assumed safe to downshift.
_KEEP_PREMIUM_SOURCES = {"lumen-chat", "lumen", "coding-ta"}


def build_recommendations(usage: dict, min_share: float = 0.05) -> dict:
    """Analyse a cost-annotated usage dict and return where spend concentrates
    plus concrete, optional model-swap suggestions with estimated savings.

    `usage` must already have run through annotate_usage (so each
    lifetime_by_source counter carries a `cost_usd`). `min_share` hides advice
    for agents below that fraction of total cost (noise).
    """
    by_source = usage.get("lifetime_by_source") or {}
    if not isinstance(by_source, dict):
        by_source = {}

    total_cost = round(sum((c.get("cost_usd", 0.0) or 0.0)
                           for c in by_source.values() if isinstance(c, dict)), 6)

    items: list[dict] = []
    potential_savings = 0.0

    for src, cnt in by_source.items():
        if not isinstance(cnt, dict):
            continue
        cur_cost = cnt.get("cost_usd", 0.0) or 0.0
        share = (cur_cost / total_cost) if total_cost else 0.0
        cur_model = model_for_source(src)
        prompt = cnt.get("prompt", 0)
        completion = cnt.get("completion", 0)

        item = {
            "source": src,
            "current_model": cur_model,
            "current_cost_usd": round(cur_cost, 6),
            "share_pct": round(share * 100, 1),
            "total_tokens": cnt.get("total", 0),
            "calls": cnt.get("calls", 0),
            "on_cheapest": cur_model not in MODEL_ALTERNATIVES,
            "advice": "",
        }

        alt = MODEL_ALTERNATIVES.get(cur_model)
        if alt and cur_cost > 0:
            projected = cost_usd(prompt, completion, model=alt["cheaper"])
            savings = round(cur_cost - projected, 6)
            item["suggested_model"] = alt["cheaper"]
            item["projected_cost_usd"] = round(projected, 6)
            item["savings_usd"] = savings
            item["savings_pct"] = round((savings / cur_cost) * 100, 1) if cur_cost else 0
            if src in _KEEP_PREMIUM_SOURCES:
                item["advice"] = (
                    f"Route the simple {src} turns to {alt['cheaper']} ({alt['matches_on']}); "
                    f"keep {cur_model} for {alt['keep_premium_for']}. "
                    f"Full swap would save ~${savings:.4f}."
                )
            else:
                item["advice"] = (
                    f"{src} can likely run entirely on {alt['cheaper']} "
                    f"({alt['matches_on']}) — est. save ${savings:.4f}."
                )
            if share >= min_share:
                potential_savings += max(savings, 0.0)
        elif item["on_cheapest"]:
            # Already on the cheapest model — only lever left is fewer/leaner tokens.
            if share >= min_share and cnt.get("calls", 0):
                avg = cnt.get("total", 0) / max(cnt.get("calls", 1), 1)
                item["advice"] = (
                    f"Already on the cheapest model. To cut further, trim prompt size "
                    f"(avg {avg:,.0f} tok/call) — shorter context or cached results."
                )
            else:
                item["advice"] = "Already on the cheapest model."

        items.append(item)

    items.sort(key=lambda x: x["current_cost_usd"], reverse=True)
    top = items[0]["source"] if items else None

    return {
        "total_cost_usd": total_cost,
        "top_cost_source": top,
        "potential_savings_usd": round(potential_savings, 6),
        "items": items,
        "model_options": MODEL_ALTERNATIVES,
    }
