"""
GitHub OAuth Device Flow — lets users log in via browser instead of
copy-pasting a Personal Access Token.

Flow:
1. Request a device code from GitHub
2. User visits https://github.com/login/device and enters the code
3. We poll GitHub until the user authorizes
4. GitHub returns an access token we can use for API calls
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

from app.config import settings

# Prefer Lumen's configured GitHub OAuth app; fall back to the demo's public
# client id so device-flow login keeps working out of the box.
GITHUB_CLIENT_ID = (
    settings.github_oauth_client_id
    or os.getenv("GITHUB_CLIENT_ID")
    or "Ov23lirg16M7rlmIDWb5"
)

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


@dataclass
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def request_device_code(
    scopes: str = "repo read:user",
    client_id: Optional[str] = None,
) -> DeviceCodeResponse:
    """Step 1: Request a device code from GitHub."""
    cid = client_id or GITHUB_CLIENT_ID
    resp = requests.post(
        DEVICE_CODE_URL,
        data={"client_id": cid, "scope": scopes},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return DeviceCodeResponse(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        expires_in=data["expires_in"],
        interval=data.get("interval", 5),
    )


def poll_for_token(
    device_code: str,
    interval: int = 5,
    timeout: int = 300,
    client_id: Optional[str] = None,
) -> str:
    """Step 2: Poll GitHub until the user has authorized (or timeout)."""
    cid = client_id or GITHUB_CLIENT_ID
    elapsed = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval

        resp = requests.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": cid,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" in data:
            return data["access_token"]

        error = data.get("error")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
            continue
        elif error == "expired_token":
            raise TimeoutError("Device code expired. Please try again.")
        elif error == "access_denied":
            raise PermissionError("User denied authorization.")
        else:
            raise RuntimeError(f"Unexpected OAuth error: {error}")

    raise TimeoutError("Timed out waiting for user authorization.")
