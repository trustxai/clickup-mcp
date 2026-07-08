"""Unit tests for the ClickUp Webhooks tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.webhooks import (
    CreateWebhookInput,
    DeleteWebhookInput,
    GetWebhooksInput,
    UpdateWebhookInput,
    clickup_create_webhook,
    clickup_delete_webhook,
    clickup_get_webhooks,
    clickup_update_webhook,
)

TEAM = "9008"


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.webhooks.get_client", lambda: fake)


# --------------------------------------------------------------------------- create
async def test_create_webhook_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "wh1", "webhook": {"id": "wh1", "health": {"status": "active"}}})
    _patch(monkeypatch, fake)

    result = await clickup_create_webhook(
        CreateWebhookInput(team_id=TEAM, endpoint="https://x.io/h", events=["taskCreated", "taskUpdated"])
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/team/{TEAM}/webhook"
    assert call["json_body"]["endpoint"] == "https://x.io/h"
    assert call["json_body"]["events"] == ["taskCreated", "taskUpdated"]
    assert "wh1" in result and "active" in result


async def test_create_webhook_wildcard_and_location_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "wh2", "webhook": {}})
    _patch(monkeypatch, fake)

    result = await clickup_create_webhook(
        CreateWebhookInput(team_id=TEAM, endpoint="https://x.io/h", events=["*"], list_id="901300")
    )

    body = fake.calls[0]["json_body"]
    assert body["events"] == ["*"]
    assert body["list_id"] == "901300"
    # unset location filters are omitted from the body
    assert "space_id" not in body and "task_id" not in body
    assert "wh2" in result


async def test_create_webhook_missing_team_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    # no team_id passed and CLICKUP_TEAM_ID stripped by the autouse fixture
    result = await clickup_create_webhook(CreateWebhookInput(endpoint="https://x.io/h", events=["taskCreated"]))

    assert result.startswith("Error")
    assert "Workspace id" in result
    assert fake.calls == []  # never reached the API


# --------------------------------------------------------------------------- get
async def test_get_webhooks_surfaces_health(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "webhooks": [
                {
                    "id": "wh1",
                    "endpoint": "https://x.io/h",
                    "events": ["taskCreated"],
                    "health": {"status": "failing", "fail_count": 5},
                }
            ]
        }
    )
    _patch(monkeypatch, fake)

    result = await clickup_get_webhooks(GetWebhooksInput(team_id=TEAM))

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/team/{TEAM}/webhook"
    assert "failing" in result  # health.status surfaced
    assert "fail_count 5" in result
    assert "taskCreated" in result


async def test_get_webhooks_empty_and_bare_array(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[])  # bare-array response shape, empty
    _patch(monkeypatch, fake)

    result = await clickup_get_webhooks(GetWebhooksInput(team_id=TEAM))

    assert "No webhooks" in result


async def test_get_webhooks_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"webhooks": [{"id": "wh1", "endpoint": "https://x.io/h"}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_webhooks(GetWebhooksInput(team_id=TEAM, response_format="json"))

    assert '"count": 1' in result
    assert "wh1" in result


# --------------------------------------------------------------------------- update
async def test_update_webhook_partial_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_update_webhook(UpdateWebhookInput(webhook_id="wh1", status="active"))

    call = fake.calls[0]
    assert call["method"] == "PUT"
    assert call["path"] == "/webhook/wh1"
    assert call["json_body"] == {"status": "active"}
    assert "wh1" in result and "status" in result


async def test_update_webhook_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        UpdateWebhookInput(webhook_id="wh1")


# --------------------------------------------------------------------------- delete
async def test_delete_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_delete_webhook(DeleteWebhookInput(webhook_id="wh1"))

    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == "/webhook/wh1"
    assert "Deleted" in result and "wh1" in result


# --------------------------------------------------------------------------- error path
async def test_get_webhooks_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("refused"))
    _patch(monkeypatch, fake)

    result = await clickup_get_webhooks(GetWebhooksInput(team_id=TEAM))

    assert result.startswith("Error")
    assert "could not connect" in result


# --------------------------------------------------------------------------- live smoke (read-only)
@pytest.mark.live
async def test_get_webhooks_live() -> None:
    result = await clickup_get_webhooks(GetWebhooksInput())
    assert isinstance(result, str)
    assert not result.startswith("Error")
