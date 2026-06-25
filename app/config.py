"""Lumen Demo — Configuration."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = "gpt-5.4"
    azure_openai_mini_deployment: str = "gpt-54-mini"
    azure_openai_api_version: str = "2024-10-21"
    azure_managed_identity_client_id: str = ""
    cosmos_endpoint: str = ""
    cosmos_database: str = "lumen-demo"
    entra_client_id: str = ""
    entra_tenant_id: str = "72f988bf-86f1-41af-91ab-2d7cd011db47"
    jwt_secret: str = "lumen-demo-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24
    port: int = 8000
    # Google OAuth (optional — leave empty to disable Google login)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    # Disk-persistent fallback store for Lumens when Cosmos is not configured.
    # On Azure App Service, /home is persistent across restarts.
    lumen_store_path: str = ""
    # Azure Speech Service (from Azure AI Foundry resource → Get API keys)
    azure_speech_key: str = ""
    azure_speech_region: str = "eastus"
    # Azure AI Foundry (for gpt-54-mini via Projects SDK)
    foundry_endpoint: str = "https://anirfoundry.services.ai.azure.com"
    foundry_project: str = "proj-anirfoundry"
    # GitHub OAuth app (web Authorization Code flow — no PAT needed).
    # Create at github.com → Settings → Developer settings → OAuth Apps.
    # Authorization callback URL must equal github_oauth_redirect_uri.
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = ""
    # Notion integration (set these to enable "Connect Notion")
    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_redirect_uri: str = ""
    # Wolfram Alpha — used by app/agents/wolfram_agent.py
    wolfram_app_id: str = "LQT3XP2TRY"
    # External Outlook device-code OAuth client ID (public client app in Azure AD)
    external_outlook_client_id: str = ""
    # Self base URL used by internal A2A HTTP self-calls.
    # Auto-derived at startup from WEBSITE_HOSTNAME on Azure App Service,
    # else http://localhost:{port}.
    app_base_url: str = ""
    # Public base domain for shareable Lumen links, e.g. "lumen.org".
    # When set, share links use the subdomain form `https://{username}.lumen.org`
    # (requires wildcard DNS + host routing). When empty, links fall back to the
    # path form `{app_base_url}/u/{username}`.
    lumen_base_domain: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
