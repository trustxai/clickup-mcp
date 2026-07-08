"""Settings for the ClickUp MCP server, loaded from env vars / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration, derived from `CLICKUP_*` environment variables.

    Every field has a default so importing the package never fails; missing
    credentials surface as a descriptive error on the first API request.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Personal API token (pk_...) or OAuth2 access token. Sent verbatim in the
    # Authorization header — ClickUp does not use a Bearer prefix for personal tokens.
    clickup_api_token: str = ""

    # Default Workspace (team) id, used by tools when the caller omits team_id.
    clickup_team_id: str = ""

    clickup_api_url: str = "https://api.clickup.com/api/v2"
    clickup_api_url_v3: str = "https://api.clickup.com/api/v3"

    clickup_request_timeout_seconds: float = 30.0

    @property
    def has_token(self) -> bool:
        return bool(self.clickup_api_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton (tests call `get_settings.cache_clear()`)."""
    return Settings()
