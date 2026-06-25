"""Auth package — JWT/Entra ID dependencies and helpers.

These are FastAPI dependencies and helper functions, not ASGI middleware.
Import from here, e.g. `from app.auth import get_current_user`.
"""

from app.auth.dependencies import (
    ENTRA_EMAIL_BLOCK_MESSAGE,
    get_current_user,
    is_entra_user,
    sign_token,
    verify_entra_token,
)

__all__ = [
    "ENTRA_EMAIL_BLOCK_MESSAGE",
    "get_current_user",
    "is_entra_user",
    "sign_token",
    "verify_entra_token",
]
