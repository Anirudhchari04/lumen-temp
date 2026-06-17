"""Benchmark Lumen agents: compare token usage, latency, and cost.

Two modes:

  python scripts/benchmark_agents.py            # simulation (default, no creds)
  python scripts/benchmark_agents.py --live     # real lumen_chat calls

Simulation mode replays representative (prompt, completion, latency) figures per
agent so you can compare the *cost mix* and spot optimisation targets without
Azure credentials. Latency in sim mode is a representative estimate, clearly
labelled. Live mode drives the real lumen_chat path and measures actual
round-trip latency + real token usage returned by the model — run it where
Azure creds / managed identity are available (e.g. the deployed App Service).

Output: a per-agent table sorted by total cost, plus optimisation notes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# UTF-8 stdout so the box-drawing chars render on a Windows console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.lumen.pricing import cost_usd, model_for_source  # noqa: E402


# ── Representative workload ──────────────────────────────────────────────────
# Each row: (agent/source, prompt_tokens, completion_tokens, est_latency_ms, sample_prompt)
# Figures are typical observed values; sample_prompt is what --live actually sends.
WORKLOAD = [
    ("lumen-router",  300,  160,   420, "show my google mails"),
    ("lumen-chat",    450,  600,  1300, "how am I doing on my calculus goals?"),
    ("notion",        800, 1200,  2600, "summarize my linked Notion page on graph theory"),
    ("drive",         600,  900,  2100, "summarize the design doc in my Drive"),
    ("gmail",         400,  600,  1500, "summarize my unread gmail"),
    ("communication", 500,  800,  1800, "draft an email to my professor asking for an extension"),
    ("calendar",      200,  300,   900, "what's on my calendar tomorrow"),
    ("arxiv",        1500, 2000,  3400, "summarize the latest arxiv paper on diffusion models"),
    ("wolfram",       200,  100,   800, "integral of x^2 sin(x) dx"),
    ("social",        100,   50,   600, "auto-reply to a peer hello"),
    ("coding-ta",     700, 1400,  2900, "write a python quicksort with tests and notes"),
]

# How many times each agent is "called" in the benchmark run.
CALLS_PER_AGENT = 3


def fmt_usd(x: float) -> str:
    if x >= 1:
        return f"${x:,.2f}"
    return f"{x*100:.4f}¢" if x < 0.01 else f"${x:.4f}"


async def run_sim() -> list[dict]:
    rows = []
    for source, p, c, lat, _prompt in WORKLOAD:
        rows.append({
            "source": source,
            "model": model_for_source(source),
            "calls": CALLS_PER_AGENT,
            "prompt": p * CALLS_PER_AGENT,
            "completion": c * CALLS_PER_AGENT,
            "latency_ms": lat,           # per-call estimate
            "cost": cost_usd(p, c, source=source) * CALLS_PER_AGENT,
        })
    return rows


async def run_live() -> list[dict]:
    """Drive the real lumen_chat path. Every prompt routes through the same
    model entry point, so we label rows by the workload's intended agent but
    report the *measured* tokens + latency."""
    from app.lumen.agent import lumen_chat
    from app.lumen.core import _lumens

    uid = "bench-user"
    _lumens[uid] = {"id": uid, "name": "Bench", "email": "bench@example.com",
                    "lumen_id": "lumen-bench", "progress": {}, "token_usage": None}

    rows = []
    for source, _p, _c, _lat, prompt in WORKLOAD:
        agg_p = agg_c = 0
        t0 = time.perf_counter()
        for _ in range(CALLS_PER_AGENT):
            try:
                res = await lumen_chat(uid, prompt)
                tu = res.get("_token_usage", {})
                agg_p += tu.get("prompt", 0)
                agg_c += tu.get("completion", 0)
            except Exception as e:
                print(f"  ! live call failed for {source}: {e}")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rows.append({
            "source": source,
            "model": "gpt-5.4 (lumen_chat path)",
            "calls": CALLS_PER_AGENT,
            "prompt": agg_p,
            "completion": agg_c,
            "latency_ms": elapsed_ms / CALLS_PER_AGENT,
            "cost": cost_usd(agg_p, agg_c, source=source),
        })
    return rows


def report(rows: list[dict], live: bool) -> None:
    rows.sort(key=lambda r: r["cost"], reverse=True)
    total_tokens = sum(r["prompt"] + r["completion"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    total_calls = sum(r["calls"] for r in rows)

    mode = "LIVE (measured)" if live else "SIMULATION (representative)"
    print("=" * 92)
    print(f"  LUMEN AGENT BENCHMARK — {mode}")
    print("=" * 92)
    print(f"  {total_calls} calls · {total_tokens:,} tokens · {fmt_usd(total_cost)} total"
          f"  ({CALLS_PER_AGENT} calls/agent)\n")

    hdr = f"  {'Agent':<16}{'Calls':>6}{'Tokens':>10}{'Avg lat':>10}{'Cost':>12}{'$/call':>11}{'% cost':>8}{'P:C':>9}"
    print(hdr)
    print("  " + "─" * 90)
    for r in rows:
        toks = r["prompt"] + r["completion"]
        pct = (r["cost"] / total_cost * 100) if total_cost else 0
        per_call = r["cost"] / r["calls"] if r["calls"] else 0
        ratio = (r["completion"] / r["prompt"]) if r["prompt"] else 0
        print(f"  {r['source']:<16}{r['calls']:>6}{toks:>10,}{r['latency_ms']:>9.0f}ms"
              f"{fmt_usd(r['cost']):>12}{fmt_usd(per_call):>11}{pct:>7.1f}%{ratio:>8.2f}x")
    print("  " + "─" * 90)
    print(f"  {'TOTAL':<16}{total_calls:>6}{total_tokens:>10,}{'':>11}{fmt_usd(total_cost):>12}\n")

    # ── Optimisation notes ───────────────────────────────────────────────────
    print("  OPTIMISATION NOTES")
    print("  " + "─" * 90)
    top = rows[0]
    print(f"  • Biggest cost driver: {top['source']} ({top['cost']/total_cost*100:.0f}% of spend). "
          f"Trim its prompt or cache results first.")
    # High output:input ratio = long generations, the expensive half (output is ~8x input on full model).
    heavy_out = max(rows, key=lambda r: (r["completion"] / r["prompt"]) if r["prompt"] else 0)
    print(f"  • Most output-heavy: {heavy_out['source']} "
          f"({(heavy_out['completion']/heavy_out['prompt']) if heavy_out['prompt'] else 0:.1f}x completion:prompt). "
          f"Output tokens cost the most — cap max_output_tokens / ask for terser answers.")
    full_cost = sum(r["cost"] for r in rows if model_for_source(r["source"]).startswith("gpt-5.4"))
    print(f"  • {fmt_usd(full_cost)} ({full_cost/total_cost*100:.0f}%) runs on the premium model. "
          f"model_router downgrades short/non-teaching prompts to mini — verify it's catching these.")
    if not live:
        print("\n  (Simulation: token figures & latency are representative. Run with --live "
              "where Azure creds exist to measure real values.)")
    print()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark Lumen agents")
    ap.add_argument("--live", action="store_true",
                    help="Make real lumen_chat calls (needs Azure creds)")
    args = ap.parse_args()

    rows = await (run_live() if args.live else run_sim())
    report(rows, live=args.live)


if __name__ == "__main__":
    asyncio.run(main())
