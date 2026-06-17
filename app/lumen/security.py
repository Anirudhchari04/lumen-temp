"""Security & Privacy Manager — consent, access control, audit.

Every inbound LITP / A2A request to a Lumen goes through `check_permission`.
Consent is per-grantee × action × data-tier with optional expiry.
Five-tier data classification: public / profile / learning / portfolio / private.
Private never leaves the Lumen regardless of consent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as _tz
UTC = _tz.utc
from typing import Literal

from app.events.bus import publish

logger = logging.getLogger(__name__)


# ── Data classification ──────────────────────────────────────
# Ordered from most-open to most-restricted.
DataTier = Literal["public", "profile", "learning", "portfolio", "private"]
TIERS: tuple[DataTier, ...] = ("public", "profile", "learning", "portfolio", "private")

Action = Literal["message", "compare", "delegate", "broadcast", "read_profile",
                 "read_learning", "read_portfolio"]

# Default tier required by each action.
ACTION_TIER: dict[Action, DataTier] = {
    "message":         "public",
    "compare":         "learning",
    "delegate":        "learning",
    "broadcast":       "public",
    "read_profile":    "profile",
    "read_learning":   "learning",
    "read_portfolio":  "portfolio",
}


def tier_rank(t: DataTier) -> int:
    return TIERS.index(t)


# ── Stores (in-memory; swap for Cosmos later) ────────────────

# Consent grants keyed by (owner_id, grantee_id_or_wildcard, action).
# Value: {tier, expires_at (iso|None), created_at}.
_consents: dict[tuple[str, str, str], dict] = {}

# Audit log (tamper-evident in prod; plain list here).
_audit: list[dict] = []
MAX_AUDIT = 2000


# ── Consent API ──────────────────────────────────────────────

def grant_consent(owner_id: str, grantee: str, action: Action,
                  tier: DataTier = "learning",
                  duration_hours: int | None = None) -> dict:
    """Owner grants `grantee` permission to take `action` at `tier` level.

    `grantee` may be a lumen_id, a user id, or "*" for network-wide.
    """
    if action not in ACTION_TIER:
        raise ValueError(f"Unknown action: {action}")
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier}")

    expires = (datetime.now(UTC) + timedelta(hours=duration_hours)).isoformat() \
        if duration_hours else None
    record = {
        "owner_id": owner_id,
        "grantee": grantee,
        "action": action,
        "tier": tier,
        "expires_at": expires,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _consents[(owner_id, grantee, action)] = record
    _log(owner_id, "consent_granted", grantee=grantee, action=action, tier=tier)
    return record


def revoke_consent(owner_id: str, grantee: str, action: Action) -> bool:
    key = (owner_id, grantee, action)
    if key in _consents:
        del _consents[key]
        _log(owner_id, "consent_revoked", grantee=grantee, action=action)
        return True
    return False


def list_consents(owner_id: str) -> list[dict]:
    return [v for (o, _, _), v in _consents.items() if o == owner_id]


def _consent_allows(owner_id: str, caller: str, action: Action,
                    required_tier: DataTier) -> dict | None:
    """Find a consent grant that allows (caller, action, required_tier).

    Matches specific grantee first, then wildcard "*". Returns the record or None.
    """
    now = datetime.now(UTC).isoformat()
    for key in ((owner_id, caller, action), (owner_id, "*", action)):
        rec = _consents.get(key)
        if not rec:
            continue
        if rec.get("expires_at") and rec["expires_at"] < now:
            continue
        if tier_rank(rec["tier"]) >= tier_rank(required_tier):
            return rec
    return None


# ── Access check ─────────────────────────────────────────────

@dataclass
class Decision:
    allow: bool
    reason: str
    tier: DataTier
    action: Action
    matched_grant: dict | None = None
    owner_id: str = ""
    caller: str = ""

    def to_dict(self) -> dict:
        return {
            "allow": self.allow,
            "reason": self.reason,
            "tier": self.tier,
            "action": self.action,
            "matched_grant": self.matched_grant,
            "owner_id": self.owner_id,
            "caller": self.caller,
        }


async def check_permission(owner_id: str, caller: str, action: Action,
                           tier: DataTier | None = None,
                           is_self: bool = False) -> Decision:
    """Decide whether `caller` may take `action` against `owner_id`'s data.

    - Self-access (is_self=True) always allowed.
    - "private" tier is never released, even with consent.
    - Otherwise: requires an active consent grant >= required tier.
    """
    required = tier or ACTION_TIER.get(action, "learning")

    if is_self:
        decision = Decision(True, "self_access", required, action,
                            owner_id=owner_id, caller=caller)
    elif required == "private":
        decision = Decision(False, "private_tier_never_released", required, action,
                            owner_id=owner_id, caller=caller)
    elif required == "public":
        decision = Decision(True, "public_tier", required, action,
                            owner_id=owner_id, caller=caller)
    else:
        grant = _consent_allows(owner_id, caller, action, required)
        if grant:
            decision = Decision(True, "consent_grant", required, action,
                                matched_grant=grant, owner_id=owner_id, caller=caller)
        else:
            decision = Decision(False, "no_consent", required, action,
                                owner_id=owner_id, caller=caller)

    _log(owner_id, "access_check", caller=caller, action=action,
         tier=required, allow=decision.allow, reason=decision.reason)
    await publish("access_checked", decision.to_dict())
    return decision


# ── Audit log ────────────────────────────────────────────────

def _log(owner_id: str, kind: str, **fields) -> dict:
    entry = {
        "id": str(uuid.uuid4())[:8],
        "owner_id": owner_id,
        "kind": kind,
        "at": datetime.now(UTC).isoformat(),
        **fields,
    }
    _audit.append(entry)
    if len(_audit) > MAX_AUDIT:
        _audit.pop(0)
    return entry


def get_audit(owner_id: str, limit: int = 100) -> list[dict]:
    return [e for e in _audit if e["owner_id"] == owner_id][-limit:]


# ── Data classification helper ───────────────────────────────

def classify_field(path: str) -> DataTier:
    """Classify a dotted Lumen-document path to a data tier.

    Used by Portfolio/Media/Learning managers before exposing a field.
    """
    p = path.lower()
    if p.startswith("social.") or p in {"name", "id", "lumen_id"}:
        return "public"
    if p.startswith(("email", "org", "bio", "expertise", "interests",
                     "preferences")):
        return "profile"
    if p.startswith(("curriculum_progress", "tc_inventory", "session_history")):
        return "learning"
    if p.startswith(("artifacts", "portfolio")):
        return "portfolio"
    return "private"
