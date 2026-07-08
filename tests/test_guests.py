"""Unit tests for the guest tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.guests import (
    AddGuestToFolderInput,
    AddGuestToListInput,
    AddGuestToTaskInput,
    EditGuestOnWorkspaceInput,
    GetGuestInput,
    InviteGuestToWorkspaceInput,
    RemoveGuestFromFolderInput,
    RemoveGuestFromListInput,
    RemoveGuestFromTaskInput,
    RemoveGuestFromWorkspaceInput,
    clickup_add_guest_to_folder,
    clickup_add_guest_to_list,
    clickup_add_guest_to_task,
    clickup_edit_guest_on_workspace,
    clickup_get_guest,
    clickup_invite_guest_to_workspace,
    clickup_remove_guest_from_folder,
    clickup_remove_guest_from_list,
    clickup_remove_guest_from_task,
    clickup_remove_guest_from_workspace,
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
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/team/1/guest/2")
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


GUEST_PAYLOAD = {
    "guest": {
        "user": {"id": 456, "username": "Contractor", "email": "contractor@example.com"},
        "can_edit_tags": True,
        "can_see_time_spent": True,
        "can_see_time_estimated": True,
        "can_create_views": True,
        "can_see_points_estimated": False,
        "custom_role_id": 7,
    },
    "shared": {"tasks": [{"id": "t1"}], "lists": [], "folders": []},
}


async def test_invite_guest_to_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"team": {"guest": GUEST_PAYLOAD["guest"]}})
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = InviteGuestToWorkspaceInput(
        team_id="123", email="contractor@example.com", can_create_views=False
    )
    result = await clickup_invite_guest_to_workspace(params)

    assert "Invited guest" in result
    assert "contractor@example.com" in result
    assert "456" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/team/123/guest"
    assert kwargs["json_body"]["can_create_views"] is False
    assert kwargs["json_body"]["email"] == "contractor@example.com"
    assert "custom_role_id" not in kwargs["json_body"]


async def test_edit_guest_on_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = EditGuestOnWorkspaceInput(team_id="123", guest_id="456", can_see_time_spent=False)
    result = await clickup_edit_guest_on_workspace(params)

    assert "Updated guest 456" in result
    assert "can_see_time_spent" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert path == "/team/123/guest/456"
    assert kwargs["json_body"] == {"can_see_time_spent": False}


async def test_edit_guest_on_workspace_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = EditGuestOnWorkspaceInput(team_id="123", guest_id="456")
    result = await clickup_edit_guest_on_workspace(params)

    assert "No fields provided" in result
    assert fake.calls[0][2]["json_body"] == {}


async def test_get_guest_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = GetGuestInput(team_id="123", guest_id="456")
    result = await clickup_get_guest(params)

    assert "Contractor" in result
    assert "contractor@example.com" in result
    assert "1 task(s)" in result
    assert fake.calls[0] == ("GET", "/team/123/guest/456", {})


async def test_get_guest_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = GetGuestInput(team_id="123", guest_id="456", response_format=ResponseFormat.JSON)
    result = await clickup_get_guest(params)

    assert '"guest"' in result
    assert "contractor@example.com" in result


async def test_get_guest_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, {"err": "Enterprise plan required", "ECODE": "GUEST_001"}))
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = GetGuestInput(team_id="123", guest_id="456")
    result = await clickup_get_guest(params)

    assert result.startswith("Error (403)")
    assert "Enterprise" in result


async def test_remove_guest_from_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = RemoveGuestFromWorkspaceInput(team_id="123", guest_id="456")
    result = await clickup_remove_guest_from_workspace(params)

    assert "Removed guest 456 from workspace 123" in result
    assert fake.calls[0] == ("DELETE", "/team/123/guest/456", {})


async def test_add_guest_to_task(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = AddGuestToTaskInput(task_id="abc123", guest_id="456", permission_level="edit")
    result = await clickup_add_guest_to_task(params)

    assert "Shared task abc123 with guest 456" in result
    assert "permission_level=edit" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/task/abc123/guest/456"
    assert kwargs["params"] == {"include_shared": True}
    assert kwargs["json_body"] == {"permission_level": "edit"}


async def test_add_guest_to_task_with_custom_task_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = AddGuestToTaskInput(
        task_id="CUSTOM-1", guest_id="456", permission_level="read", custom_task_ids=True, team_id="123"
    )
    result = await clickup_add_guest_to_task(params)

    assert "Shared task CUSTOM-1" in result
    _, _, kwargs = fake.calls[0]
    assert kwargs["params"] == {"include_shared": True, "custom_task_ids": True, "team_id": "123"}


def test_add_guest_to_task_custom_ids_requires_team_id() -> None:
    with pytest.raises(ValidationError):
        AddGuestToTaskInput(task_id="CUSTOM-1", guest_id="456", permission_level="read", custom_task_ids=True)


async def test_remove_guest_from_task(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = RemoveGuestFromTaskInput(task_id="abc123", guest_id="456")
    result = await clickup_remove_guest_from_task(params)

    assert "Removed guest 456 from task abc123" in result
    method, path, kwargs = fake.calls[0]
    assert method == "DELETE"
    assert path == "/task/abc123/guest/456"
    assert kwargs["params"] == {"include_shared": True}


async def test_add_guest_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = AddGuestToListInput(list_id="789", guest_id="456", permission_level="comment")
    result = await clickup_add_guest_to_list(params)

    assert "Shared list 789 with guest 456" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/list/789/guest/456"
    assert kwargs["json_body"] == {"permission_level": "comment"}


async def test_remove_guest_from_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = RemoveGuestFromListInput(list_id="789", guest_id="456", include_shared=False)
    result = await clickup_remove_guest_from_list(params)

    assert "Removed guest 456 from list 789" in result
    _, _, kwargs = fake.calls[0]
    assert kwargs["params"] == {"include_shared": False}


async def test_add_guest_to_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GUEST_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = AddGuestToFolderInput(folder_id="321", guest_id="456", permission_level="create")
    result = await clickup_add_guest_to_folder(params)

    assert "Shared folder 321 with guest 456" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/folder/321/guest/456"
    assert kwargs["json_body"] == {"permission_level": "create"}


async def test_remove_guest_from_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.guests.get_client", lambda: fake)

    params = RemoveGuestFromFolderInput(folder_id="321", guest_id="456")
    result = await clickup_remove_guest_from_folder(params)

    assert "Removed guest 456 from folder 321" in result
    method, path, _ = fake.calls[0]
    assert method == "DELETE"
    assert path == "/folder/321/guest/456"
