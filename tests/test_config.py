"""Unit tests for Settings and the cached singleton."""

from __future__ import annotations

import pytest

from clickup_mcp.config import Settings, get_settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.clickup_api_token == ""
    assert settings.clickup_team_id == ""
    assert settings.clickup_api_url == "https://api.clickup.com/api/v2"
    assert settings.clickup_api_url_v3 == "https://api.clickup.com/api/v3"
    assert settings.clickup_request_timeout_seconds == 30.0
    assert settings.has_token is False


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_123")
    monkeypatch.setenv("CLICKUP_TEAM_ID", "9013")
    monkeypatch.setenv("CLICKUP_REQUEST_TIMEOUT_SECONDS", "5.5")
    settings = Settings()
    assert settings.clickup_api_token == "pk_123"
    assert settings.clickup_team_id == "9013"
    assert settings.clickup_request_timeout_seconds == 5.5
    assert settings.has_token is True


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
