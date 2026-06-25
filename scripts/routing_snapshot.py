"""Routing snapshot harness — proves classify_intent behavior is preserved across
the modular-routing refactor (#1/#6). Run before and after; the SHA must match.

    python -m scripts.routing_snapshot            # prints lines + final SHA
    python -m scripts.routing_snapshot > base.txt # capture a baseline

It exercises every keyword tier (by feeding representative phrases) plus the
structural/regex tiers (emails, "open X", IDs, greetings, today/tomorrow).
"""

from __future__ import annotations

import hashlib

from app.agents.interaction_manager import classify_intent

# Representative messages covering each routing tier. Order mixes tiers so that
# precedence bugs (e.g. portfolio-before-calendar, shiksha-before-progress) show.
MESSAGES = [
    # coding-ta
    "open coding ta", "coding-ta", "coding tutor please", "show coding-ta/ folder",
    # launch
    "open calendar", "go to github", "switch to math", "launch shiksha",
    "open it", "take me to my portfolio", "open the english ta",
    # communication keywords
    "send email to bob", "check my inbox", "compose email", "connect my email",
    "disconnect outlook", "search my email", "any new email", "unread mail",
    "recent emails", "emails from alice", "notify the team",
    # sent-mail regex
    "my sent emails", "what did i send today", "show my outbox", "emails i have sent",
    # view-email regex (typo tolerant)
    "show my recemt emails", "give me my mails", "fetch new email", "read my inbox",
    # inbound-email regex
    "did rajesh send me a mail", "has alice emailed me", "any reply from priya",
    "did vedanth get back to me", "heard from sam", "any messages from bob",
    # email addr + send verb
    "send to him a follow up", "reply to alice about the report",
    "write to john@example.com about lunch", "bob@x.com send to him the notes",
    # peer message
    "send a message to priya", "message arjun", "dm sneha", "msg the group",
    # notion
    "open my notion page", "create a note", "summarize my notes", "search notion",
    # arxiv
    "find papers on RAG", "summarize the paper 2406.01234", "latest research on llms",
    "arxiv papers about diffusion",
    # wolfram
    "integrate sin x dx", "convert 5 km to miles", "solve for x in 2x+3=9",
    "boiling point of water", "wolfram alpha pi", "step by step derivative of x^2",
    # drive
    "search my google drive", "create a google doc", "my google sheets",
    "open my drive file",
    # onedrive
    "list my onedrive files", "files shared with me", "create folder reports",
    "what's in my drive",
    # outlook
    "show high importance mail", "my inbox rules", "list conference rooms",
    "email categories",
    # portfolio / github
    "show my repo", "recent commits", "what's staged", "commit staged changes",
    "open github agent", "list pull requests", "code review my branch",
    "delete from github", "my portfolio files",
    # portfolio folder regex
    "files in math ta folder", "contents in the cs-ta folder", "my portfolio",
    # calendar manage
    "cancel my 3pm meeting", "remove all events today", "postpone the exam",
    "reschedule standup",
    # calendar query
    "what's on my calendar", "my schedule this week", "upcoming events",
    "show my calendar", "this month",
    # today/tomorrow
    "today", "tomorrow", "what's today", "what's tomorrow",
    # scheduling
    "make a study plan", "remind me monday", "add holiday diwali", "set a reminder",
    # calendar create regex
    "set june 12 as my birthday on my calendar", "add a meeting tomorrow",
    "block out friday afternoon", "book a slot at 3pm",
    # on my calendar regex
    "put this on my calendar", "add to my google calendar",
    # shiksha
    "which tas are available", "my progress in english", "continue learning",
    "what did my chemistry ta cover", "open shiksha", "session history with math ta",
    # progress
    "how am i doing", "my progress", "where am i", "my score",
    # meta
    "what can you do", "list agents", "help me with something",
    # social
    "find peers", "study group", "who else is learning calculus", "compare with priya",
    # learning query
    "what have i covered", "what should i learn next",
    # learning
    "teach me calculus", "explain photosynthesis", "let's do math", "start cs",
    # general / fallthrough
    "hi", "thanks", "the weather is nice", "tell me a joke", "",
]


def main() -> int:
    lines = []
    for msg in MESSAGES:
        intent, target = classify_intent(msg)
        lines.append(f"{msg!r} -> {intent} | {target}")
    blob = "\n".join(lines)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    print(blob)
    print(f"\nROUTING_SNAPSHOT_SHA256 {digest}  ({len(MESSAGES)} messages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
