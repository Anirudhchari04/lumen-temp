"""Azure OpenAI model client for autogen, built from v1's Entra ID credentials.

Mirrors v1's auth exactly (see app/agents/llm_router.py): Entra ID via a bearer
token provider — NEVER api_key=<token>. Custom Azure deployment names like
"gpt-5.4" aren't in autogen's built-in model registry, so we declare the model's
capabilities explicitly via model_info.
"""

from __future__ import annotations

import logging

from azure.identity import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

from v2 import config

logger = logging.getLogger("lumen.v2.model")

_COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"

# Capabilities for the Lumen Azure OpenAI deployment. function_calling is required
# for MagenticOne's tool-using AssistantAgents. family="unknown" tells autogen to
# trust these explicit flags rather than infer from the (custom) deployment name.
_MODEL_INFO = {
    "vision": False,
    "function_calling": True,
    "json_output": True,
    "family": "unknown",
    "structured_output": True,
    "multiple_system_messages": True,
}


def _credential():
    if config.AZURE_MI_CLIENT_ID:
        return ManagedIdentityCredential(client_id=config.AZURE_MI_CLIENT_ID)
    return DefaultAzureCredential()


def build_model_client() -> AzureOpenAIChatCompletionClient:
    """Construct a fresh Azure OpenAI client. Caller is responsible for .close()."""
    if not config.AZURE_OPENAI_ENDPOINT:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT is not configured. Lumen v2 reuses v1's Azure "
            "OpenAI endpoint — set it in your .env / App Service settings."
        )
    token_provider = get_bearer_token_provider(_credential(), _COGNITIVE_SCOPE)
    return AzureOpenAIChatCompletionClient(
        azure_deployment=config.AZURE_OPENAI_DEPLOYMENT,
        model=config.AZURE_OPENAI_DEPLOYMENT,
        api_version=config.AZURE_OPENAI_API_VERSION,
        azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
        azure_ad_token_provider=token_provider,
        model_info=_MODEL_INFO,
    )
