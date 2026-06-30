"""Lumen Economics — credit balance + append-only ledger.

Implements the design's invariant #7 ("pay for your own lumen"): every charge
lands on the owner of the lumen that generated the work, decrements a balance,
and appends an immutable ledger entry (who, how much, why, against what). A
turn that would overdraw is refused via :func:`ensure_can_spend`.

Balances are denominated in USD-equivalent credits and stored on the lumen doc
(``credits`` + ``credit_ledger``), reusing the existing persistence path so it
works with both Cosmos and the in-memory/disk fallback. Metering reuses
``app.lumen.pricing`` so the accounting seam is identical whether the unit is a
flat per-turn cost or actual token spend.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from app.lumen.core import get_lumen, save_lumen

logger = logging.getLogger(__name__)

UTC = timezone.utc
_LEDGER_KEEP = 200  # retain the most recent N ledger entries on the doc


class InsufficientCredits(Exception):
    """Raised when a charge would overdraw an account."""


def _default_grant() -> float:
    try:
        return float(os.getenv("LUMEN_DEFAULT_CREDITS", "5.0"))
    except (TypeError, ValueError):
        return 5.0


def _enforce() -> bool:
    """Whether overdraws are refused. Off by default so metering can't break
    in-flight work; flip ``LUMEN_ENFORCE_CREDITS=1`` to enforce hard limits."""
    return os.getenv("LUMEN_ENFORCE_CREDITS", "").strip().lower() in {"1", "true", "yes"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_account(lumen: dict) -> None:
    """Seed credits/ledger on a lumen doc the first time we touch it."""
    if "credits" not in lumen or not isinstance(lumen.get("credits"), (int, float)):
        lumen["credits"] = _default_grant()
        lumen.setdefault("credit_ledger", []).append({
            "id": uuid.uuid4().hex[:12],
            "ts": _now(),
            "delta": lumen["credits"],
            "reason": "signup_grant",
            "ref": None,
            "balance_after": lumen["credits"],
        })
    lumen.setdefault("credit_ledger", [])


def _append(lumen: dict, delta: float, reason: str, ref: str | None) -> dict:
    lumen["credits"] = round(float(lumen["credits"]) + float(delta), 6)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "delta": round(float(delta), 6),
        "reason": reason,
        "ref": ref,
        "balance_after": lumen["credits"],
    }
    ledger = lumen.setdefault("credit_ledger", [])
    ledger.append(entry)
    if len(ledger) > _LEDGER_KEEP:
        del ledger[: len(ledger) - _LEDGER_KEEP]
    return entry


def apply_charge(lumen: dict, amount: float, reason: str = "turn",
                 ref: str | None = None) -> dict:
    """Charge against an already-loaded lumen doc *without* persisting.

    The caller is responsible for the surrounding ``save_lumen`` (used by the
    token tracker, which mutates and saves the doc once per call). Returns the
    appended ledger entry.
    """
    _ensure_account(lumen)
    return _append(lumen, -abs(float(amount or 0.0)), reason, ref)


async def get_balance(user_id: str) -> float:
    lumen = await get_lumen(user_id)
    if not lumen:
        return 0.0
    _ensure_account(lumen)
    return float(lumen["credits"])


async def get_ledger(user_id: str, limit: int = 50) -> list[dict]:
    lumen = await get_lumen(user_id)
    if not lumen:
        return []
    _ensure_account(lumen)
    ledger = lumen.get("credit_ledger", [])
    return list(reversed(ledger[-limit:]))


async def grant(user_id: str, amount: float, reason: str = "grant") -> float:
    """Add credits to an account. Returns the new balance."""
    if amount <= 0:
        return await get_balance(user_id)
    lumen = await get_lumen(user_id)
    if not lumen:
        return 0.0
    _ensure_account(lumen)
    _append(lumen, abs(amount), reason, None)
    await save_lumen(lumen)
    return float(lumen["credits"])


async def ensure_can_spend(user_id: str, amount: float) -> bool:
    """Pre-flight check for a turn. Raises :class:`InsufficientCredits` when
    enforcement is on and the balance can't cover ``amount``."""
    balance = await get_balance(user_id)
    if _enforce() and balance < amount:
        raise InsufficientCredits(
            f"balance {balance:.4f} < required {amount:.4f}"
        )
    return True


async def charge(user_id: str, amount: float, reason: str = "turn",
                 ref: str | None = None, enforce: bool | None = None) -> dict:
    """Charge the owner of a lumen for work it generated.

    Appends an immutable ledger entry and decrements the balance. When
    ``enforce`` (or ``LUMEN_ENFORCE_CREDITS``) is set, a charge that would
    overdraw is refused with :class:`InsufficientCredits`.

    Returns ``{"balance": float, "charged": float}``.
    """
    if not user_id or amount is None or amount <= 0:
        return {"balance": await get_balance(user_id), "charged": 0.0}

    lumen = await get_lumen(user_id)
    if not lumen:
        return {"balance": 0.0, "charged": 0.0}
    _ensure_account(lumen)

    do_enforce = _enforce() if enforce is None else enforce
    if do_enforce and float(lumen["credits"]) < float(amount):
        raise InsufficientCredits(
            f"balance {lumen['credits']:.4f} < required {amount:.4f}"
        )

    _append(lumen, -abs(float(amount)), reason, ref)
    await save_lumen(lumen)
    return {"balance": float(lumen["credits"]), "charged": round(abs(float(amount)), 6)}
