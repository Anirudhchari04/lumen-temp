"""Lumen Trust / PKI — platform signing keys, JWKS, and signed agent cards.

Implements the design's invariant #2 ("authority from the source"): agent cards
are signed (detached JWS, RFC 7515) and consumers verify them against the
platform's published key set at ``/.well-known/jwks.json``. Any edit to a card
breaks its signature.

Single platform signing key (RS256) for now. The private key is generated on
first use and persisted next to the Lumen store so it survives restarts; in
production this is swapped for a managed key store with rotation (the ``kid``
machinery here already supports multiple keys).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

from app.lumen.core import _resolve_store_path

logger = logging.getLogger(__name__)

_ALG = "RS256"
_private_key: rsa.RSAPrivateKey | None = None
_kid: str | None = None


def _key_path() -> Path:
    """Where the platform signing key is persisted (sibling of the Lumen store)."""
    return _resolve_store_path().parent / "lumen_signing_key.pem"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return _b64url(n.to_bytes(length, "big"))


def _compute_kid(pub: rsa.RSAPublicKey) -> str:
    """RFC 7638 JWK thumbprint — a stable key id derived from the public key."""
    numbers = pub.public_numbers()
    members = {
        "e": _int_to_b64url(numbers.e),
        "kty": "RSA",
        "n": _int_to_b64url(numbers.n),
    }
    canonical = json.dumps(members, sort_keys=True, separators=(",", ":")).encode()
    return _b64url(hashlib.sha256(canonical).digest())


def _load_or_create_key() -> rsa.RSAPrivateKey:
    path = _key_path()
    try:
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as e:  # corrupt/unreadable key — regenerate
        logger.warning("Could not load signing key at %s (%s); regenerating", path, e)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        logger.info("Generated new platform signing key at %s", path)
    except Exception as e:  # ephemeral key still works for this process
        logger.warning("Could not persist signing key at %s (%s); using ephemeral key", path, e)
    return key


def _key() -> rsa.RSAPrivateKey:
    global _private_key, _kid
    if _private_key is None:
        _private_key = _load_or_create_key()
        _kid = _compute_kid(_private_key.public_key())
    return _private_key


def kid() -> str:
    _key()
    return _kid  # type: ignore[return-value]


def public_jwks() -> dict:
    """Public key set served at ``/.well-known/jwks.json`` for card verification."""
    pub = _key().public_key()
    numbers = pub.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": _ALG,
                "kid": kid(),
                "n": _int_to_b64url(numbers.n),
                "e": _int_to_b64url(numbers.e),
            }
        ]
    }


def _canonical(card: dict) -> bytes:
    """Canonical bytes of a card, excluding any existing signatures."""
    payload = {k: v for k, v in card.items() if k != "signatures"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_card(card: dict) -> dict:
    """Return a copy of ``card`` with a detached JWS in its ``signatures`` field.

    The signature covers the canonical card (sans ``signatures``), so any edit
    invalidates it. Shape matches the A2A AgentCardSignature convention.
    """
    protected = {"alg": _ALG, "kid": kid()}
    protected_b64 = _b64url(json.dumps(protected, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{protected_b64}.{_b64url(_canonical(card))}".encode("ascii")
    signature = _key().sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signed = dict(card)
    signed["signatures"] = [{"protected": protected_b64, "signature": _b64url(signature)}]
    return signed


def verify_card(card: dict) -> bool:
    """Verify a card's detached JWS against the current platform key. Best-effort."""
    sigs = card.get("signatures") or []
    if not sigs:
        return False
    try:
        protected_b64 = sigs[0]["protected"]
        signature = base64.urlsafe_b64decode(sigs[0]["signature"] + "==")
        signing_input = f"{protected_b64}.{_b64url(_canonical(card))}".encode("ascii")
        _key().public_key().verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, KeyError, ValueError, Exception):
        return False
