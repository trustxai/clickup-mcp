"""Unit tests for the error-to-string mapping."""

from __future__ import annotations

from typing import Any

import httpx

from clickup_mcp.errors import handle_api_error


def _status_error(status: int, body: dict[str, Any] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/thing")
    response = httpx.Response(status, json=body or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_401_names_the_token() -> None:
    result = handle_api_error(_status_error(401, {"err": "Token invalid", "ECODE": "OAUTH_025"}))
    assert result.startswith("Error (401)")
    assert "CLICKUP_API_TOKEN" in result
    assert "Token invalid" in result
    assert "OAUTH_025" in result


def test_403_mentions_plan_gating() -> None:
    result = handle_api_error(_status_error(403, {"err": "Team not authorized"}))
    assert result.startswith("Error (403)")
    assert "Enterprise" in result


def test_404_mentions_ids() -> None:
    result = handle_api_error(_status_error(404))
    assert result.startswith("Error (404)")


def test_429_mentions_rate_limit() -> None:
    result = handle_api_error(_status_error(429, {"err": "Rate limit reached"}))
    assert result.startswith("Error (429)")
    assert "100 requests" in result


def test_timeout() -> None:
    assert "timed out" in handle_api_error(httpx.TimeoutException("slow"))


def test_connect_error() -> None:
    assert "could not connect" in handle_api_error(httpx.ConnectError("refused"))


def test_runtime_error_passthrough() -> None:
    assert handle_api_error(RuntimeError("No ClickUp credentials configured.")) == (
        "Error: No ClickUp credentials configured."
    )


def test_unexpected_exception() -> None:
    result = handle_api_error(ValueError("odd"))
    assert "unexpected failure" in result
    assert "ValueError" in result
