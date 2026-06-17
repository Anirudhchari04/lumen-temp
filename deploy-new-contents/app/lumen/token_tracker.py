"""Lumen token-usage tracker.

Tracks token usage for Lumen and sub-agent LLM calls using a `source` label.

Storage:
  - In-process per-user session counter (dict, lost on restart)
  - Persisted lifetime totals on lumen doc under `token_usage` key:
      { lifetime: {total, prompt, completion},
        daily: { "YYYY-MM-DD": {total, prompt, completion}, ... } }
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from app.lumen.core import get_lumen, save_lumen
from app.lumen.pricing import cost_usd, model_for_source

logger = logging.getLogger(__name__)

# In-memory per-user session counters. Lives as long as the process.
_session_counts: dict[str, dict] = {}

# ── Per-call cost-spike detection ────────────────────────────────────────────
# A "spike" is a single LLM call that costs much more than the user's recent
# average, or that exceeds an absolute ceiling. These are the calls worth
# investigating to cut spend — usually a premium-model call or an oversized
# prompt. Thresholds are env-overridable so prod can tune without a redeploy.
#
#   LUMEN_SPIKE_MULTIPLE     call_cost >= N × trailing avg cost/call  (default 3)
#   LUMEN_SPIKE_MIN_COST     floor so trivial calls never alert  (default $0.003)
#   LUMEN_SPIKE_ABS_COST     always alert at/above this cost  (default $0.05)
#   LUMEN_SPIKE_MAX_KEEP     how many recent spike events to retain  (default 25)


def _env_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name)
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def spike_config() -> dict:
    """Current spike thresholds (resolved from env each call so it's live-tunable)."""
    return {
        "multiple": _env_float("LUMEN_SPIKE_MULTIPLE", 3.0),
        "min_cost_usd": _env_float("LUMEN_SPIKE_MIN_COST", 0.003),
        "abs_cost_usd": _env_float("LUMEN_SPIKE_ABS_COST", 0.05),
    }


def _spike_reason(call_cost: float, baseline_avg: float, cfg: dict) -> str | None:
    """Return a human reason if this call is a spike, else None."""
    if call_cost >= cfg["abs_cost_usd"]:
        return f"costs ${call_cost:.4f} — over the ${cfg['abs_cost_usd']:.2f} per-call ceiling"
    if call_cost < cfg["min_cost_usd"]:
        return None  # too small to care about, regardless of ratio
    if baseline_avg > 0 and call_cost >= cfg["multiple"] * baseline_avg:
        x = call_cost / baseline_avg
        return f"{x:.1f}× your average call cost (${baseline_avg:.4f})"
    return None


def estimate_tokens(text: str) -> int:
    """Best-effort token count for when the LLM API didn't return usage.

    Tries tiktoken if installed (accurate), otherwise falls back to a
    char-based heuristic: ~4 chars per token for English. Underestimates
    code/non-English but the order of magnitude is right.
    """
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore
        # cl100k_base matches GPT-4 / GPT-3.5 / gpt-4o / gpt-5 family encoding
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Fallback: ~4 chars per token, never less than 1 if we have content
        return max(1, len(text) // 4)


def _today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _empty_counter() -> dict:
    return {"total": 0, "prompt": 0, "completion": 0, "calls": 0, "latency_ms_sum": 0.0}


def _merge_counter(dst: dict, total: int = 0, prompt: int = 0, completion: int = 0,
                   calls: int = 0, latency_ms_sum: float = 0.0) -> None:
    dst["total"] = dst.get("total", 0) + (total or 0)
    dst["prompt"] = dst.get("prompt", 0) + (prompt or 0)
    dst["completion"] = dst.get("completion", 0) + (completion or 0)
    dst["calls"] = dst.get("calls", 0) + (calls or 0)
    dst["latency_ms_sum"] = dst.get("latency_ms_sum", 0.0) + (latency_ms_sum or 0.0)


def _with_avg_latency(counter: dict) -> dict:
    """Return a copy of a counter with avg_latency_ms derived from the sum/calls."""
    if not isinstance(counter, dict):
        return counter
    out = dict(counter)
    calls = out.get("calls", 0)
    out["avg_latency_ms"] = round(out.get("latency_ms_sum", 0.0) / calls, 1) if calls else 0
    return out


async def record_usage(user_id: str, prompt_tokens: int, completion_tokens: int,
                        model: str = "", source: str = "lumen",
                        latency_ms: float | None = None) -> None:
    """Record a single Lumen LLM call. Updates session + lifetime persisted totals.

    Pass `latency_ms` (wall-clock round-trip of the LLM call) to track per-agent
    performance; it's averaged over calls in the usage breakdown.
    """
    if not user_id:
        return
    source = (source or "lumen").strip().lower()
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    if total <= 0:
        return
    lat = float(latency_ms) if latency_ms else 0.0
    call_cost = cost_usd(prompt_tokens, completion_tokens, model=model, source=source)

    # Session counter (in-memory)
    sess = _session_counts.setdefault(user_id, {
        **_empty_counter(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "models": {},
        "by_source": {},
    })
    _merge_counter(sess, total=total, prompt=prompt_tokens, completion=completion_tokens, calls=1, latency_ms_sum=lat)
    if model:
        sess["models"][model] = sess["models"].get(model, 0) + total
    src_sess = sess["by_source"].setdefault(source, _empty_counter())
    _merge_counter(src_sess, total=total, prompt=prompt_tokens, completion=completion_tokens, calls=1, latency_ms_sum=lat)

    # Lifetime + daily counters (persisted)
    try:
        lumen = await get_lumen(user_id)
        if not lumen:
            return
        tu = lumen.get("token_usage") or {
            "lifetime": _empty_counter(),
            "daily": {},
            "by_source": {"lifetime": {}, "daily": {}},
        }
        # Backward-compat: ensure keys exist for older records.
        if "by_source" not in tu or not isinstance(tu.get("by_source"), dict):
            tu["by_source"] = {"lifetime": {}, "daily": {}}
        tu["by_source"].setdefault("lifetime", {})
        tu["by_source"].setdefault("daily", {})

        # Capture the trailing average cost/call from PRIOR calls (before merging
        # this one in) so a spike is measured against history, not itself.
        prev = tu["lifetime"]
        prev_calls = prev.get("calls", 0)
        prev_avg_cost = (prev.get("cost_usd", 0.0) / prev_calls) if prev_calls else 0.0

        _merge_counter(tu["lifetime"], total=total, prompt=prompt_tokens, completion=completion_tokens, calls=1, latency_ms_sum=lat)
        # Track running cost on the lifetime counter so the baseline survives restarts.
        tu["lifetime"]["cost_usd"] = round(prev.get("cost_usd", 0.0) + call_cost, 6)

        today = _today_key()
        day = tu["daily"].get(today) or _empty_counter()
        _merge_counter(day, total=total, prompt=prompt_tokens, completion=completion_tokens, calls=1, latency_ms_sum=lat)
        tu["daily"][today] = day

        src_life = tu["by_source"]["lifetime"].get(source) or _empty_counter()
        _merge_counter(src_life, total=total, prompt=prompt_tokens, completion=completion_tokens, calls=1, latency_ms_sum=lat)
        tu["by_source"]["lifetime"][source] = src_life

        src_daily = tu["by_source"]["daily"].get(today) or {}
        src_day = src_daily.get(source) or _empty_counter()
        _merge_counter(src_day, total=total, prompt=prompt_tokens, completion=completion_tokens, calls=1, latency_ms_sum=lat)
        src_daily[source] = src_day
        tu["by_source"]["daily"][today] = src_daily

        # Trim daily map to last 30 entries
        if len(tu["daily"]) > 30:
            keys = sorted(tu["daily"].keys())[-30:]
            tu["daily"] = {k: tu["daily"][k] for k in keys}
        if len(tu["by_source"]["daily"]) > 30:
            keys = sorted(tu["by_source"]["daily"].keys())[-30:]
            tu["by_source"]["daily"] = {k: tu["by_source"]["daily"][k] for k in keys}

        # ── Per-call cost-spike detection ────────────────────────────────────
        cfg = spike_config()
        reason = _spike_reason(call_cost, prev_avg_cost, cfg)
        if reason:
            spikes = tu.setdefault("spikes", [])
            spikes.append({
                "id": uuid.uuid4().hex[:12],
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "model": model or model_for_source(source),
                "prompt": prompt_tokens or 0,
                "completion": completion_tokens or 0,
                "total": total,
                "cost_usd": round(call_cost, 6),
                "baseline_avg_usd": round(prev_avg_cost, 6),
                "reason": reason,
                "read": False,
            })
            # Keep only the most recent N events.
            max_keep = int(_env_float("LUMEN_SPIKE_MAX_KEEP", 25))
            if len(spikes) > max_keep:
                tu["spikes"] = spikes[-max_keep:]
            logger.info(f"Cost spike for {user_id}: {source} ${call_cost:.4f} ({reason})")

        lumen["token_usage"] = tu
        await save_lumen(lumen)
    except Exception as e:
        logger.warning(f"Token usage persist failed for {user_id}: {e}")


def get_session_usage(user_id: str) -> dict:
    """Return per-process session counters for a user (or empty)."""
    return dict(_session_counts.get(user_id) or _empty_counter())


async def get_full_usage(user_id: str) -> dict:
    """Return session + lifetime + recent-daily breakdown."""
    lumen = await get_lumen(user_id) or {}
    tu = lumen.get("token_usage") or {
        "lifetime": _empty_counter(),
        "daily": {},
        "by_source": {"lifetime": {}, "daily": {}},
    }
    by_source = tu.get("by_source") if isinstance(tu.get("by_source"), dict) else {}
    by_source_lifetime = by_source.get("lifetime") if isinstance(by_source.get("lifetime"), dict) else {}
    by_source_daily = by_source.get("daily") if isinstance(by_source.get("daily"), dict) else {}

    # Build rolling windows: today, last 7 days
    today_key = _today_key()
    today = tu["daily"].get(today_key) or _empty_counter()

    week_total = 0
    week_calls = 0
    week_latency_sum = 0.0
    week_by_source: dict[str, dict] = {}
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=7)
    for k, v in tu.get("daily", {}).items():
        try:
            d = datetime.fromisoformat(k).date()
            if d >= cutoff:
                week_total += v.get("total", 0)
                week_calls += v.get("calls", 0)
                week_latency_sum += v.get("latency_ms_sum", 0.0)
        except Exception:
            continue

    for k, source_map in by_source_daily.items():
        try:
            d = datetime.fromisoformat(k).date()
            if d < cutoff:
                continue
            if not isinstance(source_map, dict):
                continue
            for src, cnt in source_map.items():
                if not isinstance(cnt, dict):
                    continue
                bucket = week_by_source.setdefault(src, _empty_counter())
                _merge_counter(
                    bucket,
                    total=cnt.get("total", 0),
                    prompt=cnt.get("prompt", 0),
                    completion=cnt.get("completion", 0),
                    calls=cnt.get("calls", 0),
                    latency_ms_sum=cnt.get("latency_ms_sum", 0.0),
                )
        except Exception:
            continue

    today_by_source = by_source_daily.get(today_key) if isinstance(by_source_daily.get(today_key), dict) else {}

    # Backward-compat for old records that only had aggregate counters.
    lifetime_counter = tu.get("lifetime", _empty_counter())
    if not by_source_lifetime and lifetime_counter.get("total", 0):
        by_source_lifetime = {"lumen": dict(lifetime_counter)}
    if not today_by_source and today.get("total", 0):
        today_by_source = {"lumen": dict(today)}
    if not week_by_source and week_total:
        week_by_source = {
            "lumen": {
                "total": week_total,
                "prompt": 0,
                "completion": 0,
                "calls": week_calls,
            }
        }

    session_usage = get_session_usage(user_id)
    session_by_source = (session_usage.get("by_source", {})
                         if isinstance(session_usage, dict) else {})

    def _avg(source_map: dict) -> dict:
        if not isinstance(source_map, dict):
            return {}
        return {src: _with_avg_latency(cnt) for src, cnt in source_map.items()}

    def _overall_avg(counter: dict, calls: int) -> int:
        return round(counter.get("latency_ms_sum", 0.0) / calls, 1) if calls else 0

    return {
        "session": session_usage,
        "today": {"total": today.get("total", 0), "calls": today.get("calls", 0),
                  "avg_latency_ms": _overall_avg(today, today.get("calls", 0))},
        "week": {"total": week_total, "calls": week_calls,
                 "avg_latency_ms": round(week_latency_sum / week_calls, 1) if week_calls else 0},
        "lifetime": _with_avg_latency(lifetime_counter),
        "today_by_source": _avg(today_by_source),
        "week_by_source": _avg(week_by_source),
        "lifetime_by_source": _avg(by_source_lifetime),
        "session_by_source": _avg(session_by_source),
        # Most-recent cost spikes (newest first) + the active thresholds.
        "spikes": list(reversed(tu.get("spikes", [])))[:10],
        "spike_config": spike_config(),
    }


def get_unread_spikes(lumen: dict) -> list[dict]:
    """Return unread spike events from a lumen doc (newest first)."""
    tu = lumen.get("token_usage") or {}
    spikes = tu.get("spikes") or []
    return [s for s in reversed(spikes) if not s.get("read")]


async def mark_spikes_read(user_id: str, ids: list[str]) -> None:
    """Mark the given spike events read so the bell badge clears."""
    if not user_id or not ids:
        return
    try:
        lumen = await get_lumen(user_id)
        if not lumen:
            return
        tu = lumen.get("token_usage") or {}
        idset = set(ids)
        changed = False
        for s in tu.get("spikes", []):
            if s.get("id") in idset and not s.get("read"):
                s["read"] = True
                changed = True
        if changed:
            lumen["token_usage"] = tu
            await save_lumen(lumen)
    except Exception as e:
        logger.warning(f"mark_spikes_read failed for {user_id}: {e}")


def reset_session(user_id: str) -> None:
    _session_counts.pop(user_id, None)
