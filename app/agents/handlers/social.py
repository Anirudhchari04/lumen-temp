"""Social agent — peer discovery & messaging. Class-based: `SocialAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

import logging

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.agents.handlers._common import _ensure_intent

logger = logging.getLogger(__name__)


class SocialAgent(BaseAgent):
    name = "social"
    intents = (Intent.SOCIAL,)
    description = "Peer discovery & messaging"
    # Offline keyword fallback owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "peers", "peer", "study group", "who else", "other students",
        "compare", "collaborate", "partner",
        "message ", "msg ", "dm ", "send message",
    )

    @staticmethod
    def _find_peer_by_name(all_lumens: list[dict], user_id: str, name_hint: str) -> dict | None:
        """Resolve a free-text name reference (e.g. "Priya", "priya s") to a peer lumen.

        Matches are case-insensitive and prefer, in order:
          1. exact full-name match
          2. exact first-name match
          3. unique substring match on the full name
        Returns None if no unambiguous match is found.
        """
        hint = (name_hint or "").strip().lower().rstrip(".,!?:;")
        if not hint:
            return None

        candidates = [
            l for l in all_lumens
            if l.get("id") != user_id and l.get("social", {}).get("discoverable", True)
        ]

        exact_full = [l for l in candidates if l.get("name", "").strip().lower() == hint]
        if len(exact_full) == 1:
            return exact_full[0]

        exact_first = [
            l for l in candidates
            if l.get("name", "").strip().split(" ", 1)[0].lower() == hint
        ]
        if len(exact_first) == 1:
            return exact_first[0]

        substring = [l for l in candidates if hint in l.get("name", "").strip().lower()]
        if len(substring) == 1:
            return substring[0]

        return None

    async def handle(self, user_id: str, message: str) -> dict:
        """Handle social queries by fetching actual peer/group data."""
        import re
        from app.routes.lumen_social import (
            get_all_lumens_full, _anonymize_peer, _study_groups,
            _progress_summary, _find_common_topics, _collaboration_suggestions,
            build_lumen_card,
        )
        from app.lumen.core import get_lumen
        from datetime import datetime, timezone as _tz
        UTC = _tz.utc

        my_lumen = await get_lumen(user_id)
        msg = message.lower()

        # Study group creation
        if "create" in msg and "group" in msg:
            return {
                "reply": "To create a study group, use the Study Groups panel in the sidebar, or I can create one for you. What subject should the group focus on?",
                "action": "social",
                "intent": Intent.SOCIAL,
                "agent_id": None,
            }

        all_lumens = await get_all_lumens_full()

        # ── Message a peer by name: various patterns ──────
        # Pattern 1: "message <name>: <body>" or "send to <name>: <body>"
        msg_match = re.match(r"\s*(?:message|msg|dm|send)\s+(?:to\s+)?([^:]+?)\s*[:\-]\s*(.+)",
                             message, re.IGNORECASE | re.DOTALL)
        # Pattern 2: "send message to <name> saying <body>"
        if not msg_match:
            msg_match = re.match(r"\s*(?:send\s+(?:a\s+)?message\s+to|message|msg|dm)\s+(\w[\w\s]*?)\s+(?:saying|that|about)\s+(.+)",
                                 message, re.IGNORECASE | re.DOTALL)
        # Pattern 3: "send message to <name>" (no body — we'll ask)
        no_body_match = None
        if not msg_match:
            no_body_match = re.match(r"\s*(?:send\s+(?:a\s+)?message\s+to|message|msg|dm)\s+(\w[\w\s]+?)\.?\s*$",
                                     message, re.IGNORECASE)

        if no_body_match:
            name_hint = no_body_match.group(1).strip()
            peer = self._find_peer_by_name(all_lumens, user_id, name_hint)
            if peer:
                peer_name = peer.get('name', name_hint) or name_hint
                peer_first = peer_name.split()[0] if peer_name.strip() else peer_name
                return {
                    "reply": f"What message would you like me to send to **{peer_name}**?\n\nSay: *message {peer_first}: your message here*",
                    "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
                }

        if msg_match:
            name_hint, body_text = msg_match.group(1).strip(), msg_match.group(2).strip()
            peer = self._find_peer_by_name(all_lumens, user_id, name_hint)
            if not peer:
                return {
                    "reply": f"I couldn't find a peer named '{name_hint}'. Say 'show my peers' to see who's on the network.",
                    "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
                }
            # Deliver the message AND synchronously fetch the peer Lumen's reply, so the
            # conversation is interactive inline (no static "delivered" dead-end). The peer's
            # Lumen answers on their behalf from their public profile. We persist both the
            # outgoing message and the reply so the Peers thread stays in sync.
            from app.routes.lumen_social import (
                _persist_peer_message, _hydrate_peer_messages,
                _peer_lumen_autoreply, _peer_messages, _lumen_msg,
            )
            sender_name = (my_lumen or {}).get("name", "Student")
            peer_name = peer.get("name", name_hint) or name_hint
            peer_first = peer_name.split()[0] if peer_name.strip() else peer_name
            try:
                await _hydrate_peer_messages(user_id)
                out_msg = _lumen_msg(user_id, sender_name, peer["id"], peer_name, body_text)
                await _persist_peer_message(out_msg)
                conversation = [
                    m for m in _peer_messages
                    if (m.get("from_id") == user_id and m.get("to_id") == peer["id"])
                    or (m.get("from_id") == peer["id"] and m.get("to_id") == user_id)
                ]
                reply = await _peer_lumen_autoreply(
                    sender_id=user_id, sender_name=sender_name, peer=peer,
                    incoming_message=body_text, conversation_history=conversation,
                )
                reply_text = (reply or {}).get("message", "") if isinstance(reply, dict) else ""
            except Exception as e:
                logger.warning(f"Peer messaging failed: {e}")
                return {
                    "reply": f"I couldn't deliver the message to {peer_name} ({e}).",
                    "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
                }
            if reply_text:
                chat_reply = (
                    f"✉️ Sent to **{peer_name}**.\n\n"
                    f"💬 **{peer_first}'s Lumen replies:**\n\n{reply_text}"
                )
            else:
                chat_reply = f"✉️ Delivered your message to **{peer_name}**'s Lumen."
            return _ensure_intent({
                "reply": chat_reply,
                "action": "social",
                "peer_id": peer["id"],
                "peer_lumen_id": peer.get("lumen_id"),
                "protocol": "litp/1.0",
            }, Intent.SOCIAL, None)

        # ── Compare with a peer by name: "compare with <name>" ────
        cmp_match = re.match(r"\s*compare\s+(?:with|to|against)\s+(.+)",
                             message, re.IGNORECASE)
        if cmp_match:
            name_hint = cmp_match.group(1).strip().rstrip(".!?")
            peer = self._find_peer_by_name(all_lumens, user_id, name_hint)
            if not peer:
                return {
                    "reply": f"I couldn't find a peer named '{name_hint}'. Say 'show my peers' to see who's on the network.",
                    "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
                }
            if not my_lumen:
                return {
                    "reply": "I don't have your progress yet — try learning something first!",
                    "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
                }
            my_sum = _progress_summary(my_lumen)
            their_sum = _progress_summary(peer, anonymize=True)
            common = _find_common_topics(my_lumen, peer)
            suggestions = _collaboration_suggestions(my_lumen, peer)

            lines = [f"📊 Comparing you with {peer.get('name', 'peer')}:\n"]
            lines.append(f"  You:  {len(my_sum['tcs_mastered'])} mastered, "
                         f"{len(my_sum['tcs_in_progress'])} in progress")
            lines.append(f"  Them: {len(their_sum['tcs_mastered'])} mastered, "
                         f"{len(their_sum['tcs_in_progress'])} in progress")
            if common:
                lines.append(f"\nCommon topics: {', '.join(common[:5])}")
            if suggestions:
                lines.append("\n" + "\n".join(f"  • {s}" for s in suggestions))
            return {
                "reply": "\n".join(lines),
                "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
                "peer_id": peer["id"],
                "comparison": {"you": my_sum, "peer": their_sum,
                               "common_topics": common, "suggestions": suggestions},
            }

        # Peer discovery — filter: same tenant + not demo
        my_tenant = ""
        if my_lumen:
            my_lid = my_lumen.get("lumen_id", "")
            # lumen_id format: lumen://<tenant>/<user_id>
            parts = my_lid.replace("lumen://", "").split("/")
            my_tenant = parts[0] if parts else ""

        def _is_demo(l):
            if l.get("org") == "demo": return True
            if (l.get("email") or "").endswith("@demo.local"): return True
            lid = l.get("id") or ""
            if lid.startswith("peer-") or lid == "demo-guest": return True
            return False

        def _same_network(l):
            """Only show peers from the same tenant/org."""
            if not my_tenant or my_tenant == "default":
                return True  # If no tenant info, show all non-demo
            peer_lid = l.get("lumen_id", "")
            peer_tenant = peer_lid.replace("lumen://", "").split("/")[0] if "lumen://" in peer_lid else ""
            return peer_tenant == my_tenant or peer_tenant == "default"

        peers = [
            _anonymize_peer(l) for l in all_lumens
            if l["id"] != user_id
            and not _is_demo(l)
            and _same_network(l)
            and l.get("social", {}).get("discoverable", True)
        ]

        # Groups
        my_groups = [g for g in _study_groups.values() if user_id in g["members"]]
        open_groups = [g for g in _study_groups.values() if user_id not in g["members"] and len(g["members"]) < g["max_members"]]

        lines = []
        if peers:
            lines.append(f"Found {len(peers)} peer(s) on your network:\n")
            for p in peers[:5]:
                subjects = ", ".join(s["ta_name"] + f" (L{s['level']})" for s in p.get("active_subjects", []))
                lines.append(f"  **{p['name']}**")
                lines.append(f"    {p['total_sessions']} sessions, {p['tcs_mastered']} concepts mastered")
                if subjects:
                    lines.append(f"    Studying: {subjects}")
            lines.append(f"\nTo compare: 'compare with [name]'")
            lines.append(f"To message: 'message [name]: hi!'")
        else:
            lines.append("No other students on your network yet. As more people join, you'll see peers here.")

        if my_groups:
            lines.append(f"\nYour study groups: {', '.join(g['name'] for g in my_groups)}")
        if open_groups:
            lines.append(f"Open groups to join: {', '.join(g['name'] for g in open_groups)}")

        peer_cards = [{
            "type": "peers",
            "data": [{"id": p.get("id", ""), "name": p.get("name", ""),
                      "sessions": p.get("total_sessions", 0),
                      "tcs_mastered": p.get("tcs_mastered", 0),
                      "subjects": [s["ta_name"] for s in p.get("active_subjects", [])]}
                     for p in peers[:5]],
        }]

        return {
            "reply": "\n".join(lines),
            "action": "social",
            "intent": Intent.SOCIAL,
            "agent_id": None,
            "peers": peers[:5],
            "my_groups": my_groups,
            "open_groups": open_groups,
            "cards": peer_cards,
        }

    async def broker(self, env: dict) -> dict:
        return await self.handle(env["user_id"], env["message"])


agent = SocialAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_social`.
_handle_social = agent.handle
