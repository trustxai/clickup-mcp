"""Unit tests for the (Workspace member) user tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.users import (
    EditUserOnWorkspaceInput,
    GetUserInput,
    InviteUserToWorkspaceInput,
    RemoveUserFromWorkspaceInput,
    clickup_edit_user_on_workspace,
    clickup_get_user,
    clickup_invite_user_to_workspace,
    clickup_remove_user_from_workspace,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _http_error(status_code: int, body: dict[str, Any]) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/team/1/user/2")
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


USER_PAYLOAD = {
    "member": {
        "user": {
            "id": 789,
            "username": "New Hire",
            "email": "newhire@example.com",
            "role": 2,
            "custom_role": None,
        }
    }
}


async def test_invite_user_to_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"team": {"members": [{"user": {"id": 789}}]}})
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = InviteUserToWorkspaceInput(team_id="123", email="newhire@example.com", admin=False)
    result = await clickup_invite_user_to_workspace(params)

    assert "Invited" in result
    assert "newhire@example.com" in result
    assert "admin=False" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/team/123/user"
    assert kwargs["json_body"] == {"email": "newhire@example.com", "admin": False}


async def test_invite_user_to_workspace_with_custom_role(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"team": {"members": [{"user": {"id": 789}}]}})
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = InviteUserToWorkspaceInput(
        team_id="123", email="newhire@example.com", admin=True, custom_role_id=5
    )
    await clickup_invite_user_to_workspace(params)

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"] == {"email": "newhire@example.com", "admin": True, "custom_role_id": 5}


async def test_edit_user_on_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=USER_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = EditUserOnWorkspaceInput(team_id="123", user_id="789", admin=True)
    result = await clickup_edit_user_on_workspace(params)

    assert "Updated user 789" in result
    assert "admin" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert path == "/team/123/user/789"
    assert kwargs["json_body"] == {"admin": True}


async def test_edit_user_on_workspace_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=USER_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = EditUserOnWorkspaceInput(team_id="123", user_id="789")
    result = await clickup_edit_user_on_workspace(params)

    assert "No fields provided" in result
    assert fake.calls[0][2]["json_body"] == {}


async def test_get_user_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=USER_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = GetUserInput(team_id="123", user_id="789")
    result = await clickup_get_user(params)

    assert "New Hire" in result
    assert "newhire@example.com" in result
    method, path, kwargs = fake.calls[0]
    assert method == "GET"
    assert path == "/team/123/user/789"
    assert kwargs["params"] == {"include_shared": True}


async def test_get_user_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=USER_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = GetUserInput(team_id="123", user_id="789", response_format=ResponseFormat.JSON)
    result = await clickup_get_user(params)

    assert '"member"' in result
    assert "newhire@example.com" in result


async def test_get_user_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, {"err": "Enterprise plan required", "ECODE": "USER_001"}))
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = GetUserInput(team_id="123", user_id="789")
    result = await clickup_get_user(params)

    assert result.startswith("Error (403)")
    assert "Enterprise" in result


async def test_remove_user_from_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.users.get_client", lambda: fake)

    params = RemoveUserFromWorkspaceInput(team_id="123", user_id="789")
    result = await clickup_remove_user_from_workspace(params)

    assert "Removed user 789 from workspace 123" in result
    assert fake.calls[0] == ("DELETE", "/team/123/user/789", {})
