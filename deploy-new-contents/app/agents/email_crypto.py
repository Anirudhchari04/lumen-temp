"""Email credential encryption — Fernet symmetric encryption for IMAP passwords.

The encryption key is derived from JWT_SECRET (deterministic, persistent).
On Azure, set JWT_SECRET to a strong random 32+ char value.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet

from app.config import settings

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    """Derive a Fernet key from JWT_SECRET. Same key across restarts."""
    # Fernet needs 32-byte URL-safe base64 key
    key_bytes = hashlib.sha256(settings.jwt_secret.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_password(password: str) -> str:
    """Encrypt password for storage in Cosmos."""
    if not password:
        return ""
    try:
        f = _get_fernet()
        token = f.encrypt(password.encode())
        return token.decode()
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise


def decrypt_password(encrypted: str) -> str:
    """Decrypt stored password."""
    if not encrypted:
        return ""
    try:
        f = _get_fernet()
        decrypted = f.decrypt(encrypted.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return ""
