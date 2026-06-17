"""A2A v1.0.0 Agent Card models.

Every internal and external agent returns an AgentCard instance from its
get_agent_card() function. FastAPI serializes it automatically via model_dump().
"""

from __future__ import annotations

from pydantic import BaseModel


class AgentProvider(BaseModel):
    organization: str
    url: str = ""


class AgentInterface(BaseModel):
    url: str
    protocolBinding: str = "JSONRPC"
    protocolVersion: str = "1.0"


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []
    inputModes: list[str] = []


class AgentCard(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    documentationUrl: str = ""
    provider: AgentProvider
    supportedInterfaces: list[AgentInterface]
    capabilities: AgentCapabilities
    defaultInputModes: list[str] = ["text/plain"]
    defaultOutputModes: list[str] = ["text/plain"]
    securitySchemes: dict = {}
    securityRequirements: list[dict] = []
    skills: list[AgentSkill] = []
