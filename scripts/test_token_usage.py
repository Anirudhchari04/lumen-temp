"""Test script: Simulate token usage across agents and display results.

Run with:
    python scripts/test_token_usage.py
"""

import asyncio
import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.lumen.token_tracker import record_usage, get_full_usage, _session_counts, _empty_counter
from app.lumen.core import _lumens  # in-memory store (no Cosmos needed for test)


# ── Seed fake lumen docs so record_usage can persist ─────────────────────────

TEST_USERS = [
    {"id": "user-alice", "name": "Alice Johnson", "email": "alice@example.com", "lumen_id": "lumen-alice"},
    {"id": "user-bob", "name": "Bob Chen", "email": "bob@example.com", "lumen_id": "lumen-bob"},
    {"id": "user-carol", "name": "Carol Patel", "email": "carol@example.com", "lumen_id": "lumen-carol"},
]

# Simulated call data: (user_id, prompt_tokens, completion_tokens, model, source)
SIMULATED_CALLS = [
    # Alice — heavy Lumen + communication user
    ("user-alice", 320, 180, "gpt-54-mini", "lumen-router"),   # routing call
    ("user-alice", 450, 600, "gpt-54-mini", "lumen-chat"),     # general chat
    ("user-alice", 280, 350, "gpt-54-mini", "lumen-router"),   # another routing call
    ("user-alice", 500, 800, "gpt-54-mini", "communication"),  # email compose
    ("user-alice", 200, 300, "gpt-54-mini", "communication"),  # email refine
    ("user-alice", 150, 100, "gpt-54-mini", "calendar"),       # study plan
    ("user-alice", 800, 1200, "gpt-54-mini", "notion"),        # page summarize
    ("user-alice", 100, 50, "gpt-54-mini", "social"),          # peer auto-reply

    # Bob — research-heavy user
    ("user-bob", 300, 150, "gpt-54-mini", "lumen-router"),     # routing
    ("user-bob", 400, 500, "gpt-54-mini", "lumen-chat"),       # general chat
    ("user-bob", 1500, 2000, "gpt-54-mini", "arxiv"),          # paper summarize
    ("user-bob", 1200, 1800, "gpt-54-mini", "arxiv"),          # another paper
    ("user-bob", 600, 900, "gpt-54-mini", "drive"),            # doc summarize
    ("user-bob", 200, 100, "gpt-54-mini", "wolfram"),          # math query

    # Carol — calendar + email user
    ("user-carol", 250, 120, "gpt-54-mini", "lumen-router"),   # routing
    ("user-carol", 350, 400, "gpt-54-mini", "lumen-chat"),     # general chat
    ("user-carol", 180, 250, "gpt-54-mini", "calendar"),       # event query
    ("user-carol", 300, 450, "gpt-54-mini", "calendar"),       # study plan
    ("user-carol", 600, 900, "gpt-54-mini", "communication"),  # email compose
    ("user-carol", 400, 600, "gpt-54-mini", "gmail"),          # gmail summarize
]


async def main():
    # Seed in-memory lumen store
    for user in TEST_USERS:
        _lumens[user["id"]] = {**user, "progress": {}, "token_usage": None}

    print("=" * 70)
    print("  LUMEN TOKEN USAGE SIMULATION")
    print("=" * 70)
    print(f"\n  Simulating {len(SIMULATED_CALLS)} LLM calls across {len(TEST_USERS)} users...\n")

    # Record all simulated usage
    for uid, prompt, completion, model, source in SIMULATED_CALLS:
        await record_usage(uid, prompt, completion, model=model, source=source)

    # Display results per user
    for user in TEST_USERS:
        uid = user["id"]
        usage = await get_full_usage(uid)
        lifetime = usage.get("lifetime", {})
        by_source = usage.get("lifetime_by_source", {})

        print(f"\n{'─' * 70}")
        print(f"  {user['name']} ({user['email']})")
        print(f"{'─' * 70}")
        print(f"  Lifetime total: {lifetime.get('total', 0):,} tokens | {lifetime.get('calls', 0)} calls")
        print(f"  {'Source':<20} {'Tokens':>10} {'Calls':>8} {'Prompt':>10} {'Completion':>12}")
        print(f"  {'─' * 62}")
        for src, cnt in sorted(by_source.items(), key=lambda x: x[1].get("total", 0), reverse=True):
            print(f"  {src:<20} {cnt.get('total', 0):>10,} {cnt.get('calls', 0):>8} {cnt.get('prompt', 0):>10,} {cnt.get('completion', 0):>12,}")

    # Aggregate across all users
    print(f"\n\n{'=' * 70}")
    print("  AGGREGATE (ALL USERS)")
    print("=" * 70)

    aggregate_by_source: dict = {}
    grand_total = 0
    grand_calls = 0

    for user in TEST_USERS:
        usage = await get_full_usage(user["id"])
        lifetime = usage.get("lifetime", {})
        grand_total += lifetime.get("total", 0)
        grand_calls += lifetime.get("calls", 0)
        for src, cnt in usage.get("lifetime_by_source", {}).items():
            agg = aggregate_by_source.setdefault(src, {"total": 0, "prompt": 0, "completion": 0, "calls": 0})
            agg["total"] += cnt.get("total", 0)
            agg["prompt"] += cnt.get("prompt", 0)
            agg["completion"] += cnt.get("completion", 0)
            agg["calls"] += cnt.get("calls", 0)

    print(f"\n  Grand total: {grand_total:,} tokens | {grand_calls} calls")
    print(f"\n  {'Source':<20} {'Tokens':>10} {'% of Total':>12} {'Calls':>8}")
    print(f"  {'─' * 52}")
    for src, cnt in sorted(aggregate_by_source.items(), key=lambda x: x[1]["total"], reverse=True):
        pct = (cnt["total"] / grand_total * 100) if grand_total else 0
        print(f"  {src:<20} {cnt['total']:>10,} {pct:>10.1f}% {cnt['calls']:>8}")

    # Show the API response format
    print(f"\n\n{'=' * 70}")
    print("  API ENDPOINT RESPONSE FORMAT: GET /lumen/usage/tokens/all")
    print("=" * 70)

    api_response = {
        "aggregate_total_tokens": grand_total,
        "aggregate_by_source": aggregate_by_source,
        "users": [
            {
                "user_id": user["id"],
                "name": user["name"],
                "lifetime_total": (await get_full_usage(user["id"])).get("lifetime", {}).get("total", 0),
                "lifetime_calls": (await get_full_usage(user["id"])).get("lifetime", {}).get("calls", 0),
                "by_source": (await get_full_usage(user["id"])).get("lifetime_by_source", {}),
            }
            for user in TEST_USERS
        ],
    }
    print(f"\n{json.dumps(api_response, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
