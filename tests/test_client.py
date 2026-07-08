"""Unit tests for ClickUpClient using httpx.MockTransport."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from clickup_mcp.client import ClickUpClient
from clickup_mcp.config import Settings


def _client_with(handler: Any, **settings_kwargs: Any) -> ClickUpClient:
    settings = Settings(clickup_api_token="pk_test", **settings_kwargs)
    return ClickUpClient(settings=settings, transport=httpx.MockTransport(handler))


async def test_auth_header_sent_verbatim_and_v2_base() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    resp = await client.request("GET", "/user")

    assert resp.status_code == 200
    # ClickUp personal tokens are sent verbatim — no Bearer prefix.
    assert captured["authorization"] == "pk_test"
    assert captured["url"] == "https://api.clickup.com/api/v2/user"


async def test_use_v3_switches_base_url() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={})

    client = _client_with(handler)
    await client.request("GET", "workspaces/123/docs", use_v3=True)

    assert captured["url"] == "https://api.clickup.com/api/v3/workspaces/123/docs"


async def test_params_and_json_body_forwarded() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={})

    client = _client_with(handler)
    await client.request("POST", "/list/1/task", params={"custom_task_ids": "true"}, json_body={"name": "t"})

    assert "custom_task_ids=true" in captured["url"]
    assert '"name"' in captured["body"]


async def test_missing_token_raises_runtime_error() -> None:
    client = ClickUpClient(settings=Settings())
    with pytest.raises(RuntimeError, match="CLICKUP_API_TOKEN"):
        await client.request("GET", "/user")


async def test_http_error_raises_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"err": "Rate limit reached", "ECODE": "SHARD_001"})

    client = _client_with(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.request("GET", "/user")
