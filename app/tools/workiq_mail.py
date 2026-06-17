"""WorkIQ Mail MCP Client.

Calls the Microsoft WorkIQ Mail MCP server using the user's Entra token
(OAuth Identity Passthrough). No admin consent required — the token is
forwarded directly, so the call runs as the signed-in user.

Endpoint: https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools
"""

from __future__ import annotations

import logging
import uuid
import httpx

logger = logging.getLogger(__name__)

WORKIQ_MAIL_URL = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools"


async def _mcp_call(user_token: str, method: str, params: dict) -> dict:
    """Make a JSON-RPC 2.0 call to the WorkIQ MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": method,
        "params": params,
    }
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(WORKIQ_MAIL_URL, json=payload, headers=headers)
        resp.raise_for_status()
        # MCP may return SSE or JSON
        ct = resp.headers.get("content-type", "")
        if "event-stream" in ct:
            # Parse SSE — collect all data lines
            result_data = {}
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    import json
                    try:
                        result_data = json.loads(line[5:].strip())
                    except Exception:
                        pass
            return result_data
        return resp.json()


async def list_tools(user_token: str) -> list[dict]:
    """Discover available tools on the WorkIQ Mail MCP server."""
    try:
        result = await _mcp_call(user_token, "tools/list", {})
        return result.get("result", {}).get("tools", [])
    except Exception as e:
        logger.warning(f"WorkIQ tools/list failed: {e}")
        return []


async def send_email(user_token: str, to_email: str, subject: str, body: str) -> dict:
    """Send an email via WorkIQ Mail MCP.

    Returns: {"status": "sent"|"failed", "error": str|None}
    """
    # Try common WorkIQ tool names for send
    tool_names = ["send_email", "SendEmail", "mail.send", "createAndSendMessage", "sendMail"]

    # First, try to discover the real tool name
    try:
        tools = await list_tools(user_token)
        if tools:
            send_tools = [t for t in tools if "send" in t.get("name", "").lower()]
            if send_tools:
                tool_names = [send_tools[0]["name"]] + tool_names
                logger.info(f"WorkIQ discovered send tool: {send_tools[0]['name']}")
    except Exception:
        pass

    last_error = None
    for tool_name in tool_names:
        try:
            result = await _mcp_call(
                user_token,
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": {
                        "to": to_email,
                        "subject": subject,
                        "body": body,
                        "bodyType": "text",
                        "saveToSentItems": True,
                    },
                },
            )
            # Check for success
            rpc_result = result.get("result", {})
            error = result.get("error")
            if error:
                last_error = f"MCP error ({tool_name}): {error.get('message', str(error))}"
                logger.warning(last_error)
                continue
            logger.info(f"WorkIQ email sent via {tool_name} to {to_email}")
            return {"status": "sent", "method": f"workiq/{tool_name}", "result": rpc_result}
        except httpx.HTTPStatusError as e:
            last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.warning(f"WorkIQ call failed ({tool_name}): {last_error}")
            # 401/403 → stop trying, token issue
            if e.response.status_code in (401, 403):
                break
        except Exception as e:
            last_error = str(e)
            logger.warning(f"WorkIQ call exception ({tool_name}): {e}")

    return {"status": "failed", "error": last_error or "WorkIQ MCP call failed"}
