"""Lumen-to-Lumen — Peer discovery, comparison, and collaboration.

Enables Lumens (students) to discover each other, compare progress,
and form study groups. All data is anonymized by default.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone as _tz
UTC = _tz.utc

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lumen.core import get_lumen, get_all_lumens, save_lumen
from app.middleware.auth import get_current_user
from app.db.cosmos import (
    is_cosmos_ready,
    add_peer_message,
    get_peer_messages_for_user,
    mark_peer_messages_read as cosmos_mark_peer_messages_read,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["lumen-social"])


# ── Peer Discovery ───────────────────────────────────────────

@router.get("/peers")
async def discover_peers(current_user: dict = Depends(get_current_user)):
    """Discover other Lumens on the network. Each peer comes with its LITP card."""
    my_lumen = await get_lumen(current_user["id"])
    if not my_lumen:
        return {"peers": [], "count": 0, "protocol": "litp/1.0"}

    all_lumens = await get_all_lumens_full()
    peers = []
    for lumen in all_lumens:
        if lumen["id"] == current_user["id"]:
            continue
        # Filter out demo / dummy accounts that may linger in the store.
        if lumen.get("org") == "demo":
            continue
        if (lumen.get("email") or "").endswith("@demo.local"):
            continue
        if lumen.get("id", "").startswith("peer-") or lumen.get("id") == "demo-guest":
            continue
        if not lumen.get("social", {}).get("discoverable", True):
            continue
        summary = _anonymize_peer(lumen)
        summary["card"] = build_lumen_card(lumen)
        peers.append(summary)

    return {"peers": peers, "count": len(peers), "protocol": "litp/1.0"}


@router.get("/compare/{peer_id}")
async def compare_with_peer(peer_id: str, current_user: dict = Depends(get_current_user)):
    """Compare your progress with a peer's (anonymized)."""
    my_lumen = await get_lumen(current_user["id"])
    peer_lumen = await get_lumen(peer_id)

    if not my_lumen or not peer_lumen:
        raise HTTPException(status_code=404, detail="Lumen not found")

    if not peer_lumen.get("social", {}).get("discoverable", True):
        raise HTTPException(status_code=403, detail="Peer is not discoverable")

    return {
        "you": _progress_summary(my_lumen),
        "peer": _progress_summary(peer_lumen, anonymize=True),
        "common_topics": _find_common_topics(my_lumen, peer_lumen),
        "suggestions": _collaboration_suggestions(my_lumen, peer_lumen),
    }


# ── Study Groups ─────────────────────────────────────────────

class StudyGroupCreate(BaseModel):
    name: str
    subject: str = ""
    max_members: int = 10

class StudyGroupJoin(BaseModel):
    group_id: str

# In-memory study groups (would be Cosmos in production)
_study_groups: dict[str, dict] = {}


@router.post("/groups")
async def create_study_group(body: StudyGroupCreate, current_user: dict = Depends(get_current_user)):
    """Create a study group."""
    import uuid
    group_id = str(uuid.uuid4())[:8]
    group = {
        "id": group_id,
        "name": body.name,
        "subject": body.subject,
        "creator_id": current_user["id"],
        "members": [current_user["id"]],
        "max_members": body.max_members,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _study_groups[group_id] = group

    from app.events.bus import publish, PEER_CONNECTED
    await publish(PEER_CONNECTED, {"group_id": group_id, "user_id": current_user["id"], "action": "created"})

    return {"ok": True, "group": group}


@router.post("/groups/join")
async def join_study_group(body: StudyGroupJoin, current_user: dict = Depends(get_current_user)):
    """Join an existing study group."""
    group = _study_groups.get(body.group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Study group not found")
    if len(group["members"]) >= group["max_members"]:
        raise HTTPException(status_code=400, detail="Study group is full")
    if current_user["id"] in group["members"]:
        return {"ok": True, "message": "Already a member", "group": group}

    group["members"].append(current_user["id"])

    from app.events.bus import publish, PEER_CONNECTED
    await publish(PEER_CONNECTED, {"group_id": body.group_id, "user_id": current_user["id"], "action": "joined"})

    return {"ok": True, "group": group}


@router.get("/groups")
async def list_study_groups(current_user: dict = Depends(get_current_user)):
    """List study groups the user belongs to, plus open groups."""
    my_groups = [g for g in _study_groups.values() if current_user["id"] in g["members"]]
    open_groups = [
        g for g in _study_groups.values()
        if current_user["id"] not in g["members"] and len(g["members"]) < g["max_members"]
    ]
    return {"my_groups": my_groups, "open_groups": open_groups}


@router.get("/groups/{group_id}/progress")
async def group_progress(group_id: str, current_user: dict = Depends(get_current_user)):
    """See aggregated progress of a study group (anonymized)."""
    group = _study_groups.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Study group not found")
    if current_user["id"] not in group["members"]:
        raise HTTPException(status_code=403, detail="Not a member of this group")

    member_progress = []
    for member_id in group["members"]:
        lumen = await get_lumen(member_id)
        if lumen:
            member_progress.append(_progress_summary(lumen, anonymize=(member_id != current_user["id"])))

    return {
        "group": {"id": group["id"], "name": group["name"], "member_count": len(group["members"])},
        "members": member_progress,
    }


# ── Social Settings ──────────────────────────────────────────

class SocialSettings(BaseModel):
    discoverable: bool = True
    share_progress: bool = True

@router.put("/social-settings")
async def update_social_settings(body: SocialSettings, current_user: dict = Depends(get_current_user)):
    """Update social/privacy settings."""
    lumen = await get_lumen(current_user["id"])
    if not lumen:
        raise HTTPException(status_code=404, detail="Lumen not found")
    lumen.setdefault("social", {})
    lumen["social"]["discoverable"] = body.discoverable
    lumen["social"]["share_progress"] = body.share_progress
    await save_lumen(lumen)
    return {"ok": True, "social": lumen["social"]}


# ── Peer Messaging ──────────────────────────────────────────

# In-memory message store. Acts as the read cache + fallback when Cosmos is
# unavailable; persisted writes go through `_persist_peer_message()` which
# also fires off a Cosmos upsert.
_peer_messages: list[dict] = []
_peer_messages_loaded_for: set[str] = set()


async def _persist_peer_message(msg: dict) -> dict:
    """Append to in-memory cache and (if available) to Cosmos.

    Returns the message (with channel_id added) so callers can use the same
    dict in their response payloads.
    """
    if is_cosmos_ready():
        try:
            stored = await add_peer_message(msg)
            # Mutate the original dict so any in-flight references stay in sync.
            msg.update(stored)
        except Exception as e:
            logger.warning(f"peer message persist failed (kept in-memory): {e}")
    _peer_messages.append(msg)
    return msg


async def _hydrate_peer_messages(user_id: str) -> None:
    """Lazily pull this user's persisted history into the in-memory cache.

    Cosmos is the source of truth, but every read site in this module iterates
    `_peer_messages` directly. Hydrate once per user per process so the
    in-memory cache reflects history written before this server boot (or by
    other instances during scale-out).
    """
    if not is_cosmos_ready() or user_id in _peer_messages_loaded_for:
        return
    _peer_messages_loaded_for.add(user_id)
    try:
        existing_ids = {m.get("id") for m in _peer_messages}
        for stored in await get_peer_messages_for_user(user_id):
            if stored.get("id") and stored["id"] not in existing_ids:
                _peer_messages.append(stored)
    except Exception as e:
        logger.warning(f"peer message hydrate failed: {e}")


class PeerMessage(BaseModel):
    to_id: str
    message: str


@router.post("/message")
async def send_peer_message(body: PeerMessage, current_user: dict = Depends(get_current_user)):
    """Send a message peer-to-peer. Your Lumen delivers it to their Lumen on your behalf.
    The target peer's Lumen replies on the peer's behalf using their public profile."""
    peer = await get_lumen(body.to_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")

    sender_name = current_user.get("name", "Student")
    await _hydrate_peer_messages(current_user["id"])
    msg = {
        "id": str(__import__("uuid").uuid4())[:8],
        "kind": "chat",
        "from_id": current_user["id"],
        "from_name": sender_name,
        "sender_display": f"{sender_name.split(' ')[0]}'s Lumen",
        "from_lumen": True,
        "to_id": body.to_id,
        "to_name": peer.get("name", "Student"),
        "message": body.message,
        "read": False,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await _persist_peer_message(msg)

    # Kick off an auto-reply from the peer's Lumen (fire-and-forget).
    # Include conversation history so the peer's Lumen can respond in context.
    import asyncio as _asyncio
    conversation = [
        m for m in _peer_messages
        if (m["from_id"] == current_user["id"] and m["to_id"] == body.to_id)
        or (m["from_id"] == body.to_id and m["to_id"] == current_user["id"])
    ]
    _asyncio.create_task(_peer_lumen_autoreply(
        sender_id=current_user["id"], sender_name=sender_name,
        peer=peer, incoming_message=body.message,
        conversation_history=conversation,
    ))

    return {"ok": True, "message": msg}


async def _peer_lumen_autoreply(sender_id: str, sender_name: str, peer: dict,
                               incoming_message: str, conversation_history: list | None = None) -> None:
    """The peer's Lumen composes a reply on the peer's behalf, using their public
    profile (bio/expertise/interests) and conversation history as context."""
    try:
        peer_name = peer.get("name", "Student")
        first = (peer_name.split(" ")[0] if peer_name else "Peer") or "Peer"
        visibility = peer.get("visibility") or {}

        # ── Private-info gating ──────────────────────────────────────
        # If the message asks for a personal field, honour the peer's visibility:
        #   public  → share the value directly
        #   private → don't reveal; open an approval request for the peer to accept/deny
        requested_field = _detect_requested_field(incoming_message)
        if requested_field:
            label = _FIELD_LABEL.get(requested_field, requested_field)
            field_value = (peer.get(requested_field) or "").strip()
            field_vis = visibility.get(requested_field, "private")
            if field_vis == "public":
                reply_text = (
                    f"Sure — {first}'s {label} is {field_value}."
                    if field_value else
                    f"{first} hasn't added their {label} yet, but it's public so I'll share it as soon as they do."
                )
            else:
                # Queue a pending approval request (deduped) so the peer can decide.
                already = any(
                    r for r in _info_requests
                    if r.get("from_id") == sender_id and r.get("to_id") == peer["id"]
                    and r.get("field") == requested_field and r.get("status") == "pending"
                )
                if not already:
                    _info_requests.append({
                        "id": str(uuid.uuid4())[:8],
                        "from_id": sender_id, "from_name": sender_name,
                        "to_id": peer["id"], "to_name": peer_name,
                        "field": requested_field, "field_label": label,
                        "reason": (incoming_message or "")[:200],
                        "status": "pending",
                        "created_at": datetime.now(UTC).isoformat(),
                    })
                reply_text = (
                    f"{first}'s {label} is private, so I can't share it outright — but I've sent {first} "
                    f"a request to approve it. You'll hear back here once they respond."
                )
            reply = _lumen_msg(peer["id"], peer_name, sender_id, sender_name, reply_text)
            reply["auto_reply"] = True
            await _persist_peer_message(reply)
            return reply

        # Only include what the peer has marked public
        public_bits = []
        if peer.get("bio") and visibility.get("bio", "public") == "public":
            public_bits.append(f"Bio: {peer['bio']}")
        if peer.get("expertise") and visibility.get("expertise", "public") == "public":
            public_bits.append(f"Good at: {peer['expertise']}")
        if peer.get("interests") and visibility.get("interests", "public") == "public":
            public_bits.append(f"Interests: {peer['interests']}")

        progress = peer.get("curriculum_progress", {}) or {}
        subjects = []
        for ta_id, d in progress.items():
            ta_name = d.get("ta_name", ta_id)
            level = d.get("current_level", 1)
            label = d.get("level_label", "beginner")
            sessions = d.get("session_count", 0)
            mastered = d.get("topics_mastered", [])
            covered = d.get("topics_covered", [])
            module = d.get("current_module", "")
            subjects.append(f"{ta_name} (Level {level} {label}, {sessions} sessions)")
            if covered:
                public_bits.append(f"Topics covered in {ta_name}: {', '.join(covered[:5])}")
            if mastered:
                public_bits.append(f"Mastered in {ta_name}: {', '.join(mastered[:5])}")
        if subjects:
            public_bits.append("Currently studying: " + ", ".join(subjects))

        has_progress = bool(progress)
        context_block = "\n".join(public_bits) if public_bits else "(no public profile details yet)"

        # Build conversation context
        conv_lines = []
        for m in (conversation_history or [])[-8:]:
            who = f"{sender_name}'s Lumen" if m.get("from_id") == sender_id else f"{first}'s Lumen"
            conv_lines.append(f"{who}: {m.get('message', '')}")
        conv_block = "\n".join(conv_lines) if conv_lines else "(first message)"

        # When the peer has no curriculum_progress yet, the literal truth is
        # "I don't have any study data" \u2014 but that's a dead-end answer. Tell
        # the model what to do in that case so the reply is still useful:
        # offer to relay the question, suggest the peer log a session, or
        # reflect what little the public profile does say.
        sparse_guidance = ""
        if not has_progress:
            sparse_guidance = (
                f"\nIMPORTANT: {first} hasn't logged study sessions yet, so you have no progress "
                f"data. Do NOT just say 'no data' and do NOT say {first} is offline/unavailable. Instead:\n"
                f"  - Answer naturally as {first}'s agent using their bio/expertise/interests above.\n"
                f"  - If asked about progress/topics/level, say {first} is just getting started and "
                f"engage with the topic warmly (e.g. ask what they're studying, suggest studying together).\n"
                f"  - Keep it to 1-3 sentences, first-person as the Lumen."
            )

        from app.agents.prompt_kit import build_agent_prompt
        base = build_agent_prompt(
            role=f"{first}'s Lumen (peer-network agent)",
            mission=(
                f"Represent {first} to other students' Lumens on the Lumen peer network and reply on "
                f"{first}'s behalf using their public profile."
            ),
            capabilities=[
                f"Speak to {first}'s learning, progress, interests, and availability from their public profile.",
                "Hold a natural back-and-forth with another student's Lumen.",
                "Offer to set up study sessions or collaboration.",
            ],
            rules=[
                f"Reply directly and helpfully as {first}'s Lumen, first-person (not as {first}), in 1-3 sentences.",
                f"Never say {first} is offline, away, or unavailable, and don't just promise to 'pass it along' \u2014 answer now.",
                "Only use the details provided below; never invent personal facts.",
                "NEVER repeat an earlier message. Read the conversation so far and ADVANCE it \u2014 respond to what was just said, ask a follow-up, or propose a next step. Vary your wording every turn.",
                f"If asked about progress, cite specifics from the profile. If asked to meet or collaborate, respond warmly and say you'll get it onto {first}'s calendar.",
            ],
            output_format="Plain text \u2014 1-3 warm, natural sentences, first-person as the Lumen.",
        )
        prompt = (
            f"{base}\n"
            + (f"{sparse_guidance}\n" if sparse_guidance else "")
            + f"\n{first}'S PUBLIC PROFILE:\n{context_block}\n\n"
            f"CONVERSATION SO FAR:\n{conv_block}\n\n"
            f"LATEST MESSAGE FROM {sender_name}'s Lumen:\n\"{incoming_message}\"\n\n"
            f"Reply now as {first}'s Lumen:"
        )

        try:
            from app.config import settings
            from openai import AsyncAzureOpenAI
            from azure.identity import (ManagedIdentityCredential as SyncMI,
                                        DefaultAzureCredential as SyncDAC,
                                        get_bearer_token_provider)

            # Use an AAD bearer-token PROVIDER (not api_key=<token>). Passing an
            # Entra token as api_key makes Azure OpenAI 401 — which is exactly why
            # this autoreply used to fail every time and fall back to a canned line.
            sync_cred = (SyncMI(client_id=settings.azure_managed_identity_client_id)
                         if settings.azure_managed_identity_client_id else SyncDAC())
            token_provider = get_bearer_token_provider(
                sync_cred, "https://cognitiveservices.azure.com/.default")
            aoai = AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                azure_ad_token_provider=token_provider,
            )
            messages_list = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": incoming_message},
            ]
            # max_completion_tokens (not max_tokens) — the mini deployment rejects
            # max_tokens, another reason the old call always errored out.
            result = await aoai.chat.completions.create(
                model=settings.azure_openai_mini_deployment or settings.azure_openai_deployment,
                messages=messages_list,
                max_completion_tokens=260,
                temperature=0.7,
            )
            reply_text = (result.choices[0].message.content or "").strip()[:600]
            if not reply_text:
                raise ValueError("empty LLM reply")

            try:
                from app.lumen.token_tracker import record_usage, estimate_tokens
                usage = getattr(result, "usage", None)
                prompt_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
                completion_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0
                if prompt_tokens == 0 and completion_tokens == 0:
                    prompt_tokens = estimate_tokens(prompt + "\n" + incoming_message)
                    completion_tokens = estimate_tokens(reply_text)
                await record_usage(peer["id"], prompt_tokens, completion_tokens,
                                   model=(settings.azure_openai_mini_deployment or settings.azure_openai_deployment or "aoai"),
                                   source="social")
            except Exception:
                pass
        except Exception as e:
            logger.info(f"peer autoreply LLM fallback: {e}")
            if subjects:
                reply_text = (
                    f"Hi! I'm {first}'s Lumen. {first} is currently studying "
                    f"{', '.join(subjects[:2])} — happy to compare notes or set up a study session. "
                    f"What did you want to dig into?"
                )
            else:
                reply_text = (
                    f"Hey! I'm {first}'s Lumen. {first} is just getting started, so there's not much "
                    f"progress to share yet — but I'm here and glad you reached out. Want to study "
                    f"something together?"
                )

        reply = _lumen_msg(
            peer["id"], peer_name, sender_id, sender_name, reply_text
        )
        reply["auto_reply"] = True
        await _persist_peer_message(reply)
        return reply
    except Exception as e:
        logger.warning(f"peer autoreply failed: {e}")
        return None


# ── Private-info requests ────────────────────────────────────

_info_requests: list[dict] = []

_REQUESTABLE_FIELDS = {"phone", "address", "dob", "occupation", "bio"}
_FIELD_LABEL = {
    "phone": "phone number", "address": "address", "dob": "date of birth",
    "occupation": "occupation", "bio": "bio",
}

# Phrases that signal a peer is asking for a personal field. Used by the peer
# autoreply to honour visibility: public → share; private → approval request.
_FIELD_PATTERNS = {
    "phone": ["phone", "mobile", "whatsapp", "contact number", "your number", "their number", "cell"],
    "address": ["address", "where do you live", "where does", "where are you based", "home town", "hometown", "your location"],
    "dob": ["birthday", "date of birth", "dob", "when were you born", "how old", "your age"],
    "occupation": ["occupation", "your job", "what's your job", "what do you do for work", "profession", "where do you work", "what do you do"],
    "bio": ["your bio", "about yourself", "tell me about you", "your background"],
}


def _detect_requested_field(message: str) -> str | None:
    """Return the personal field a message is asking for, or None."""
    m = (message or "").lower()
    for field, phrases in _FIELD_PATTERNS.items():
        if any(p in m for p in phrases):
            return field
    return None


class InfoRequest(BaseModel):
    to_id: str
    field: str
    reason: str | None = None


def _lumen_msg(from_user_id: str, from_name: str, to_id: str, to_name: str,
               message: str, kind: str = "chat") -> dict:
    return {
        "id": str(__import__("uuid").uuid4())[:8],
        "kind": kind,
        "from_id": from_user_id,
        "from_name": from_name,
        "sender_display": f"{from_name.split(' ')[0]}'s Lumen" if from_name else "Lumen",
        "from_lumen": True,
        "to_id": to_id,
        "to_name": to_name,
        "message": message,
        "read": False,
        "created_at": datetime.now(UTC).isoformat(),
    }


@router.post("/info-request")
async def create_info_request(body: InfoRequest, current_user: dict = Depends(get_current_user)):
    """Ask a peer's Lumen to share a private field. Auto-grants if target marked field public."""
    if body.field not in _REQUESTABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"Field must be one of {sorted(_REQUESTABLE_FIELDS)}")
    peer = await get_lumen(body.to_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")

    visibility = (peer.get("visibility") or {})
    field_value = peer.get(body.field, "") or ""
    requester_name = current_user.get("name", "Student")
    peer_name = peer.get("name", "Student")
    label = _FIELD_LABEL.get(body.field, body.field)

    # Public → auto-grant immediately via Lumen-delivered message
    if visibility.get(body.field, "private") == "public":
        if not field_value:
            msg = f"{peer_name} hasn't shared their {label} yet, but it's marked public."
        else:
            msg = f"Here's {peer_name}'s {label} (marked public): **{field_value}**"
        await _persist_peer_message(_lumen_msg(
            body.to_id, peer_name, current_user["id"], requester_name, msg))
        return {"ok": True, "status": "auto_granted", "field": body.field}

    # Private → queue request
    req = {
        "id": str(__import__("uuid").uuid4())[:8],
        "from_id": current_user["id"],
        "from_name": requester_name,
        "to_id": body.to_id,
        "to_name": peer_name,
        "field": body.field,
        "field_label": label,
        "reason": (body.reason or "")[:200],
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
    }
    _info_requests.append(req)
    return {"ok": True, "status": "pending", "request": req}


@router.get("/info-requests/pending")
async def list_pending_info_requests(current_user: dict = Depends(get_current_user)):
    """Info-requests awaiting the current user's approval."""
    mine = [r for r in _info_requests
            if r["to_id"] == current_user["id"] and r["status"] == "pending"]
    return {"requests": mine, "count": len(mine)}


@router.get("/info-requests/history")
async def info_request_history(current_user: dict = Depends(get_current_user)):
    """All info-requests where the user is sender or recipient, newest first."""
    uid = current_user["id"]
    rows = [r for r in _info_requests if r["from_id"] == uid or r["to_id"] == uid]
    rows = sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)
    for r in rows:
        r["direction"] = "outgoing" if r["from_id"] == uid else "incoming"
    return {"requests": rows, "count": len(rows)}


@router.get("/notifications/feed")
async def notifications_feed(current_user: dict = Depends(get_current_user)):
    """Unified proactive feed for the NotificationsBell — merges pending info-requests,
    unread calendar reminders, and recent unread peer messages."""
    uid = current_user["id"]
    await _hydrate_peer_messages(uid)

    items: list[dict] = []

    # 1. Pending info-requests (needs Yes/No action)
    for r in _info_requests:
        if r["to_id"] == uid and r["status"] == "pending":
            items.append({
                "type": "info_request",
                "id": r["id"],
                "title": f"{r['from_name']}'s Lumen wants your {r.get('field_label') or r['field']}",
                "subtitle": r.get("reason") or "",
                "created_at": r["created_at"],
                "payload": r,
            })

    # 2. Unread calendar reminders
    try:
        from app.agents.calendar_agent import get_notifications
        for n in get_notifications(uid, unread_only=True):
            kind = n.get("kind", "reminder")
            items.append({
                "type": "calendar_" + kind,
                "id": n["id"],
                "title": ("Reminder: " if kind == "reminder" else "Starting now: ") + n.get("title", ""),
                "subtitle": n.get("when", ""),
                "created_at": n.get("created_at", ""),
                "payload": n,
            })
    except Exception as e:
        logger.info(f"feed: calendar fetch failed: {e}")

    # 3. Unread Lumen-mediated peer messages (last 10)
    unread_msgs = [m for m in _peer_messages
                   if m.get("to_id") == uid and not m.get("read")]
    unread_msgs = sorted(unread_msgs, key=lambda m: m.get("created_at", ""), reverse=True)[:10]
    for m in unread_msgs:
        items.append({
            "type": "peer_message",
            "id": m["id"],
            "title": f"{m.get('sender_display') or m.get('from_name', 'Peer')}",
            "subtitle": (m.get("message") or "")[:120],
            "created_at": m.get("created_at", ""),
            "payload": m,
        })

    # 4. Unread cost spikes (expensive single LLM calls worth investigating)
    try:
        from app.lumen.core import get_lumen
        from app.lumen.token_tracker import get_unread_spikes
        lumen = await get_lumen(uid)
        if lumen:
            for s in get_unread_spikes(lumen):
                items.append({
                    "type": "cost_spike",
                    "id": s["id"],
                    "title": f"Cost spike: {s.get('source', 'agent')}",
                    "subtitle": s.get("reason", ""),
                    "created_at": s.get("ts", ""),
                    "payload": s,
                })
    except Exception as e:
        logger.info(f"feed: cost-spike fetch failed: {e}")

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"items": items, "count": len(items)}


class MarkReadBody(BaseModel):
    type: str   # calendar | message
    ids: list[str]


@router.post("/notifications/mark-read")
async def mark_feed_items_read(body: MarkReadBody, current_user: dict = Depends(get_current_user)):
    """Mark calendar / message notifications as read so the bell badge clears."""
    uid = current_user["id"]
    if body.type == "calendar":
        from app.agents.calendar_agent import mark_notifications_read
        mark_notifications_read(uid, body.ids)
    elif body.type == "spike":
        from app.lumen.token_tracker import mark_spikes_read
        await mark_spikes_read(uid, body.ids)
    elif body.type == "message":
        for m in _peer_messages:
            if m.get("id") in body.ids and m.get("to_id") == uid:
                m["read"] = True
        # Persist the read flag so it survives restarts and is visible to other instances.
        if is_cosmos_ready():
            try:
                await cosmos_mark_peer_messages_read(uid, body.ids)
            except Exception as e:
                logger.warning(f"cosmos mark-read failed (in-memory only): {e}")
    return {"ok": True}


class InfoRespond(BaseModel):
    request_id: str
    accept: bool


@router.post("/info-request/respond")
async def respond_info_request(body: InfoRespond, current_user: dict = Depends(get_current_user)):
    """Approve/deny a pending info-request. On approve, your Lumen DMs the value to theirs."""
    req = next((r for r in _info_requests if r["id"] == body.request_id), None)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["to_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not your request")
    if req["status"] != "pending":
        return {"ok": True, "status": req["status"]}

    me = await get_lumen(current_user["id"])
    my_name = current_user.get("name", "Student")

    if body.accept:
        value = (me or {}).get(req["field"], "") or ""
        if not value:
            text = f"I'd like to share my {req['field_label']}, but I haven't added it to my profile yet."
        else:
            text = f"Sharing my {req['field_label']} with you: **{value}**\n\n_(One-time share, approved via LITP.)_"
        await _persist_peer_message(_lumen_msg(
            current_user["id"], my_name, req["from_id"], req["from_name"], text))
        req["status"] = "approved"
    else:
        await _persist_peer_message(_lumen_msg(
            current_user["id"], my_name, req["from_id"], req["from_name"],
            f"{my_name.split(' ')[0]} isn't sharing their {req['field_label']} right now."))
        req["status"] = "denied"
    req["responded_at"] = datetime.now(UTC).isoformat()
    return {"ok": True, "status": req["status"], "request": req}


@router.get("/messages")
async def get_peer_messages(current_user: dict = Depends(get_current_user)):
    """Get messages for the current user (inbox + sent)."""
    await _hydrate_peer_messages(current_user["id"])
    inbox = [m for m in _peer_messages if m["to_id"] == current_user["id"]]
    sent = [m for m in _peer_messages if m["from_id"] == current_user["id"]]
    return {"inbox": inbox, "sent": sent}


@router.get("/messages/unread")
async def get_unread_count(current_user: dict = Depends(get_current_user)):
    """Get unread message count."""
    await _hydrate_peer_messages(current_user["id"])
    unread = [m for m in _peer_messages if m["to_id"] == current_user["id"] and not m["read"]]
    return {"unread": len(unread)}


# ── Helpers ──────────────────────────────────────────────────

async def get_all_lumens_full() -> list[dict]:
    """Get all lumens with full data (for peer comparison)."""
    if is_cosmos_ready():
        from app.db.cosmos import query_all_lumens
        return await query_all_lumens()
    from app.lumen.core import _lumens
    return list(_lumens.values())


def _anonymize_peer(lumen: dict) -> dict:
    """Return peer info with first name visible, plus LITP addressing."""
    progress = lumen.get("curriculum_progress", {})
    tc_inv = lumen.get("tc_inventory", {"mastered": [], "in_progress": []})

    # Build subject summaries
    subjects = []
    for ta_id, data in progress.items():
        subjects.append({
            "ta_id": ta_id,
            "ta_name": data.get("ta_name", ta_id),
            "level": data.get("current_level", 1),
            "module": data.get("current_module", ""),
            "sessions": data.get("session_count", 0),
        })

    return {
        "id": lumen["id"],
        "lumen_id": lumen.get("lumen_id", f"lumen://default/{lumen['id']}"),
        "name": lumen.get("name", "Student"),
        "card_url": f"/lumen/cards/{lumen['id']}",
        "protocol": "litp/1.0",
        "active_subjects": subjects,
        "total_sessions": sum(p.get("session_count", 0) for p in progress.values()),
        "tcs_mastered": len(tc_inv.get("mastered", [])),
        "tcs_in_progress": len(tc_inv.get("in_progress", [])),
    }


def build_lumen_card(lumen: dict) -> dict:
    """Return a public Lumen card — the LITP equivalent of an agent card.

    Identifies a Lumen as an addressable peer agent: lumen_id, endpoint,
    protocol, and advertised capabilities derived from curriculum progress.
    """
    progress = lumen.get("curriculum_progress", {})
    tc_inv = lumen.get("tc_inventory", {"mastered": [], "in_progress": []})
    subjects = sorted({p.get("ta_name", tid) for tid, p in progress.items()})
    mastered_ids = [t["tc_id"] for t in tc_inv.get("mastered", [])]

    return {
        "id": lumen["id"],
        "lumen_id": lumen.get("lumen_id", f"lumen://default/{lumen['id']}"),
        "name": lumen.get("name", "Student"),
        "type": "lumen",
        "protocol": "litp/1.0",
        "endpoint": f"/lumen/connect/{lumen['id']}",
        "card_url": f"/lumen/cards/{lumen['id']}",
        "discoverable": lumen.get("social", {}).get("discoverable", True),
        "capabilities": {
            "subjects": subjects,
            "tcs_mastered": mastered_ids,
            "can_receive": ["message", "compare"],
        },
        "updated_at": lumen.get("updated_at"),
    }


@router.get("/cards/{peer_id}")
async def get_lumen_card(peer_id: str):
    """Public LITP discovery endpoint — returns a peer's Lumen card.

    Mirrors /agents/{id}/agent.json but for Lumens (person-centric agents).
    Only discoverable peers are exposed.
    """
    peer = await get_lumen(peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Lumen not found")
    if not peer.get("social", {}).get("discoverable", True):
        raise HTTPException(status_code=403, detail="Lumen is not discoverable")
    return build_lumen_card(peer)


@router.get("/cards")
async def list_lumen_cards():
    """List all discoverable Lumen cards on the network."""
    all_lumens = await get_all_lumens_full()
    cards = [build_lumen_card(l) for l in all_lumens
             if l.get("social", {}).get("discoverable", True)]
    return {"cards": cards, "count": len(cards), "protocol": "litp/1.0"}


class LITPConnectBody(BaseModel):
    """LITP connect payload — sent to a peer's Lumen endpoint.

    op: one of INTENT (default — deliver a message), COMPARE (share learning
    state summary), DELEGATE (forward a task to the peer's TA), BROADCAST
    (post to a study-group channel), NEGOTIATE (request consent), APPROVE
    (grant a pending approval).
    """
    op: str = "INTENT"
    action: str = "message"   # legacy alias; action overrides op when provided
    message: str | None = None
    from_lumen_id: str | None = None
    corr_id: str | None = None
    # NEGOTIATE / APPROVE fields
    requested_action: str | None = None
    requested_tier: str | None = None
    duration_hours: int | None = None
    # BROADCAST fields
    group_id: str | None = None


@router.post("/connect/{peer_id}")
async def litp_connect(peer_id: str, body: LITPConnectBody,
                       current_user: dict = Depends(get_current_user)):
    """LITP connect — route a request to a peer's Lumen agent using its card.

    Every inbound op passes through the Security & Privacy Manager before any
    data leaves the peer's Lumen. Public-tier ops (INTENT message) are allowed
    by default; higher-tier ops (COMPARE) require a consent grant.
    """
    from app.lumen.security import check_permission, grant_consent

    peer = await get_lumen(peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer Lumen not found")
    if not peer.get("social", {}).get("discoverable", True):
        raise HTTPException(status_code=403, detail="Peer is not discoverable")

    card = build_lumen_card(peer)
    me = await get_lumen(current_user["id"])
    caller_lumen_id = (me or {}).get("lumen_id", body.from_lumen_id or "")
    op = (body.op or "INTENT").upper()

    # ── NEGOTIATE — caller asks the peer for consent. ──
    if op == "NEGOTIATE":
        if not body.requested_action:
            raise HTTPException(400, "requested_action required for NEGOTIATE")
        # Record a pending consent request in the audit log; real peer approval
        # would come via APPROVE from the peer's owner. For demo we grant
        # immediately if the peer has `social.auto_consent = True`.
        auto = peer.get("social", {}).get("auto_consent", False)
        if auto:
            rec = grant_consent(peer["id"], caller_lumen_id or current_user["id"],
                                body.requested_action,
                                tier=body.requested_tier or "learning",
                                duration_hours=body.duration_hours)
            return {"ok": True, "op": "NEGOTIATE", "status": "granted",
                    "grant": rec, "protocol": "litp/1.0"}
        return {"ok": True, "op": "NEGOTIATE", "status": "pending",
                "message": "Awaiting peer approval (APPROVE op)",
                "protocol": "litp/1.0"}

    # ── APPROVE — the authenticated user (peer owner) grants pending consent. ──
    if op == "APPROVE":
        # Caller must be approving grants against THEIR OWN lumen.
        if peer["id"] != current_user["id"]:
            raise HTTPException(403, "APPROVE must target your own Lumen")
        if not body.requested_action:
            raise HTTPException(400, "requested_action required for APPROVE")
        rec = grant_consent(current_user["id"],
                            body.from_lumen_id or "*",
                            body.requested_action,
                            tier=body.requested_tier or "learning",
                            duration_hours=body.duration_hours)
        return {"ok": True, "op": "APPROVE", "grant": rec, "protocol": "litp/1.0"}

    # ── Everything else is gated by the Security & Privacy Manager. ──
    act = body.action if body.action != "message" or op == "INTENT" else op.lower()
    action_map = {"INTENT": "message", "COMPARE": "compare",
                  "DELEGATE": "delegate", "BROADCAST": "broadcast"}
    access_action = action_map.get(op, body.action)

    decision = await check_permission(
        owner_id=peer["id"],
        caller=caller_lumen_id or current_user["id"],
        action=access_action,
        is_self=(peer["id"] == current_user["id"]),
    )
    if not decision.allow:
        raise HTTPException(status_code=403,
                            detail=f"Access denied: {decision.reason}")

    # ── INTENT — deliver a message. ──
    if op == "INTENT" or access_action == "message":
        if not body.message:
            raise HTTPException(status_code=400, detail="message required")
        msg = {
            "id": str(__import__("uuid").uuid4())[:8],
            "from_id": current_user["id"],
            "from_lumen_id": caller_lumen_id,
            "from_name": (me or {}).get("name", current_user.get("name", "Student")),
            "to_id": peer["id"],
            "to_lumen_id": card["lumen_id"],
            "to_name": peer.get("name", "Student"),
            "message": body.message,
            "read": False,
            "protocol": "litp/1.0",
            "corr_id": body.corr_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        await _persist_peer_message(msg)
        from app.events.bus import publish, PEER_CONNECTED
        await publish(PEER_CONNECTED, {"from": msg["from_lumen_id"],
                                        "to": msg["to_lumen_id"],
                                        "op": "INTENT"})
        return {"ok": True, "op": "INTENT", "protocol": "litp/1.0",
                "delivered_to": card["lumen_id"], "message": msg}

    # ── COMPARE — return a comparison summary. ──
    if op == "COMPARE" or access_action == "compare":
        if not me:
            raise HTTPException(status_code=404, detail="Your Lumen not found")
        return {
            "ok": True, "op": "COMPARE", "protocol": "litp/1.0",
            "delivered_to": card["lumen_id"],
            "comparison": {
                "you": _progress_summary(me),
                "peer": _progress_summary(peer, anonymize=True),
                "common_topics": _find_common_topics(me, peer),
                "suggestions": _collaboration_suggestions(me, peer),
            },
        }

    # ── DELEGATE — forward a task to the peer's TA. ──
    if op == "DELEGATE":
        if not body.message:
            raise HTTPException(400, "message required for DELEGATE")
        # For the demo, "peer's TA" means routing through the peer's
        # curriculum_progress to pick the best-matching TA, then replying as
        # if the peer's Lumen dispatched it.
        from app.agents.interaction_manager import dispatch_to_ta
        from app.orchestrator.registry import detect_ta
        ta_id = detect_ta(body.message) or "math-ta"
        result = await dispatch_to_ta(peer["id"], ta_id, body.message)
        return {"ok": True, "op": "DELEGATE", "protocol": "litp/1.0",
                "delivered_to": card["lumen_id"],
                "ta_id": ta_id, "result": result}

    # ── BROADCAST — study-group channel publish. ──
    if op == "BROADCAST":
        group = _study_groups.get(body.group_id or "")
        if not group:
            raise HTTPException(404, "Study group not found")
        if peer["id"] not in group["members"]:
            raise HTTPException(403, "Peer is not in this group")
        from app.events.bus import publish
        await publish("group_message", {
            "group_id": group["id"], "from": caller_lumen_id,
            "to": card["lumen_id"], "message": body.message,
        })
        return {"ok": True, "op": "BROADCAST", "protocol": "litp/1.0",
                "group_id": group["id"]}

    raise HTTPException(status_code=400, detail=f"Unknown op: {op}")


# ── Security & Privacy endpoints ─────────────────────────────

class ConsentBody(BaseModel):
    grantee: str
    action: str
    tier: str = "learning"
    duration_hours: int | None = None


@router.post("/consent")
async def grant_consent_route(body: ConsentBody,
                              current_user: dict = Depends(get_current_user)):
    from app.lumen.security import grant_consent
    rec = grant_consent(current_user["id"], body.grantee, body.action,
                        body.tier, body.duration_hours)
    return {"ok": True, "grant": rec}


@router.delete("/consent")
async def revoke_consent_route(grantee: str, action: str,
                               current_user: dict = Depends(get_current_user)):
    from app.lumen.security import revoke_consent
    ok = revoke_consent(current_user["id"], grantee, action)
    return {"ok": ok}


@router.get("/consent")
async def list_consent_route(current_user: dict = Depends(get_current_user)):
    from app.lumen.security import list_consents
    return {"grants": list_consents(current_user["id"])}


@router.get("/audit")
async def audit_route(current_user: dict = Depends(get_current_user),
                      limit: int = 100):
    from app.lumen.security import get_audit
    return {"events": get_audit(current_user["id"], limit)}


def _progress_summary(lumen: dict, anonymize: bool = False) -> dict:
    """Build a progress summary for comparison."""
    progress = lumen.get("curriculum_progress", {})
    tc_inv = lumen.get("tc_inventory", {"mastered": [], "in_progress": []})

    subjects = {}
    for ta_id, data in progress.items():
        subjects[ta_id] = {
            "level": data.get("current_level", 1),
            "module": data.get("current_module", ""),
            "sessions": data.get("session_count", 0),
            "topics_mastered": len(data.get("topics_mastered", [])),
            "topics_covered": len(data.get("topics_covered", [])),
        }

    result = {
        "name": (lumen.get("name", "Student")[:1] + "***") if anonymize else lumen.get("name", "Student"),
        "subjects": subjects,
        "tcs_mastered": [t["tc_id"] for t in tc_inv.get("mastered", [])],
        "tcs_in_progress": [{"tc_id": t["tc_id"], "pct": t.get("progress_pct", 0)} for t in tc_inv.get("in_progress", [])],
    }
    return result


def _find_common_topics(lumen1: dict, lumen2: dict) -> list[str]:
    """Find topics both students are working on."""
    p1 = lumen1.get("curriculum_progress", {})
    p2 = lumen2.get("curriculum_progress", {})
    common_tas = set(p1.keys()) & set(p2.keys())

    common = []
    for ta_id in common_tas:
        t1 = set(p1[ta_id].get("topics_covered", []))
        t2 = set(p2[ta_id].get("topics_covered", []))
        common.extend(t1 & t2)
    return common


def _collaboration_suggestions(my_lumen: dict, peer_lumen: dict) -> list[str]:
    """Suggest collaboration opportunities."""
    suggestions = []
    my_tc = {t["tc_id"] for t in my_lumen.get("tc_inventory", {}).get("mastered", [])}
    peer_tc = {t["tc_id"] for t in peer_lumen.get("tc_inventory", {}).get("mastered", [])}

    i_can_help = my_tc - peer_tc
    they_can_help = peer_tc - my_tc

    if i_can_help:
        suggestions.append(f"You could help with: {', '.join(list(i_can_help)[:3])}")
    if they_can_help:
        suggestions.append(f"They could help you with: {', '.join(list(they_can_help)[:3])}")
    if not suggestions:
        suggestions.append("You're at similar levels — great study partners!")
    return suggestions
