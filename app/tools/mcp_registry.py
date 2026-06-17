"""MCP Tool Registry — Configures MCP tool servers for Lumen agents.

These tools connect to Microsoft Foundry Remote MCP servers,
giving agents access to real Outlook, Teams, GitHub, and web search.

Usage:
    from app.tools.mcp_registry import get_mcp_tools
    tools = await get_mcp_tools()
    agent = client.as_agent(name="Lumen", tools=tools, ...)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MCP_SERVERS = {
    "outlook-mail": {
        "name": "Microsoft Outlook Mail",
        "description": "Send, read, search, and reply to Outlook emails",
        "env_var": "MCP_OUTLOOK_MAIL_URL",
        "capabilities": ["send_email", "read_email", "search_email", "reply_email"],
    },
    "outlook-calendar": {
        "name": "Microsoft Outlook Calendar",
        "description": "Create, update, manage Outlook calendar events",
        "env_var": "MCP_OUTLOOK_CALENDAR_URL",
        "capabilities": ["create_event", "list_events", "update_event", "delete_event"],
    },
    "teams": {
        "name": "Microsoft Teams",
        "description": "Send Teams messages, manage chats and channels",
        "env_var": "MCP_TEAMS_URL",
        "capabilities": ["send_message", "create_chat", "list_chats"],
    },
    "github": {
        "name": "GitHub",
        "description": "Access GitHub repos, issues, PRs, and code",
        "env_var": "MCP_GITHUB_URL",
        "capabilities": ["search_repos", "get_file", "list_issues", "search_code"],
    },
    "web-search": {
        "name": "Web Search (Tavily)",
        "description": "Real-time web search with source citations",
        "env_var": "MCP_WEB_SEARCH_URL",
        "capabilities": ["web_search", "extract_content"],
    },
    "azure-speech": {
        "name": "Azure Speech",
        "description": "Neural TTS/STT via Azure Speech Services",
        "env_var": "MCP_AZURE_SPEECH_URL",
        "capabilities": ["text_to_speech", "speech_to_text"],
    },
}


async def get_mcp_tools() -> list:
    """Get all configured MCP tools as Agent Framework tool objects."""
    from agent_framework import MCPStreamableHTTPTool

    tools = []
    for server_id, config in MCP_SERVERS.items():
        url = os.environ.get(config["env_var"], "")
        if not url:
            continue
        try:
            tool = MCPStreamableHTTPTool(url=url, name=config["name"])
            tools.append(tool)
            logger.info(f"MCP tool registered: {config['name']} at {url}")
        except Exception as e:
            logger.warning(f"Failed to register MCP tool {config['name']}: {e}")
    return tools


def get_configured_tools() -> list[dict]:
    """Return which MCP tools are configured (for UI display)."""
    return [
        {
            "id": sid,
            "name": cfg["name"],
            "description": cfg["description"],
            "configured": bool(os.environ.get(cfg["env_var"], "")),
            "capabilities": cfg["capabilities"],
        }
        for sid, cfg in MCP_SERVERS.items()
    ]
