"""Unit tests for the views tool module against a fake client."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.views import (
    CreateFolderViewInput,
    CreateListViewInput,
    CreateSpaceViewInput,
    CreateTeamViewInput,
    DeleteViewInput,
    GetFolderViewsInput,
    GetListViewsInput,
    GetSpaceViewsInput,
    GetTeamViewsInput,
    GetViewInput,
    GetViewTasksInput,
    UpdateViewInput,
    clickup_create_folder_view,
    clickup_create_list_view,
    clickup_create_space_view,
    clickup_create_team_view,
    clickup_delete_view,
    clickup_get_folder_views,
    clickup_get_list_views,
    clickup_get_space_views,
    clickup_get_team_views,
    clickup_get_view,
    clickup_get_view_tasks,
    clickup_update_view,
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


VIEW: dict[str, Any] = {
    "id": "abc123",
    "name": "Sprint Board",
    "type": "board",
    "parent": {"id": "901", "type": 6},
    "date_created": "1000000000000",
    "grouping": {"field": "status"},
    "divide": {},
    "sorting": {"fields": []},
    "filters": {"op": "AND", "fields": []},
    "columns": {"fields": []},
    "team_sidebar": {},
    "settings": {"show_task_locations": False},
}

_SHARED_CONFIG_KEYS = {"grouping", "divide", "sorting", "filters", "columns", "team_sidebar", "settings"}


# --- create tools -----------------------------------------------------------


async def test_create_team_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_create_team_view(CreateTeamViewInput(team_id="123", name="Sprint Board", type="board"))

    assert "Created" in result
    assert "abc123" in result
    assert "Workspace 123" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/team/123/view"
    body = kwargs["json_body"]
    assert body["name"] == "Sprint Board"
    assert body["type"] == "board"
    assert _SHARED_CONFIG_KEYS <= set(body.keys())


async def test_create_space_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_create_space_view(
        CreateSpaceViewInput(space_id="456", name="Sprint Board", type="board", grouping={"field": "assignee"})
    )

    assert "Created" in result
    assert "Space 456" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/space/456/view"
    assert kwargs["json_body"]["grouping"] == {"field": "assignee"}


async def test_create_folder_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_create_folder_view(CreateFolderViewInput(folder_id="789", name="Backlog Table", type="table"))

    assert "Created" in result
    assert "Folder 789" in result
    method, path, _kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/folder/789/view"


async def test_create_list_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_create_list_view(CreateListViewInput(list_id="901", name="Sprint Calendar", type="calendar"))

    assert "Created" in result
    assert "List 901" in result
    method, path, _kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/list/901/view"


async def test_create_view_extra_field_forbidden() -> None:
    with pytest.raises(Exception):
        CreateTeamViewInput(team_id="123", name="x", type="board", bogus="nope")  # type: ignore[call-arg]


# --- get-views-at-location tools -------------------------------------------


async def test_get_team_views_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"views": [VIEW]})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_team_views(GetTeamViewsInput(team_id="123"))

    assert "Sprint Board" in result
    assert "abc123" in result
    assert fake.calls[0][:2] == ("GET", "/team/123/view")


async def test_get_space_views_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"views": [VIEW]})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_space_views(GetSpaceViewsInput(space_id="456", response_format=ResponseFormat.JSON))

    assert '"views"' in result
    assert "abc123" in result
    assert fake.calls[0][:2] == ("GET", "/space/456/view")


async def test_get_folder_views_with_required_views(monkeypatch: pytest.MonkeyPatch) -> None:
    required_view = {**VIEW, "id": "req1", "name": "Default List"}
    fake = _FakeClient(payload={"views": [], "required_views": {"list": required_view}})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_folder_views(GetFolderViewsInput(folder_id="789"))

    assert "No views found" in result
    assert "Built-in (required) views" in result
    assert "Default List" in result
    assert fake.calls[0][:2] == ("GET", "/folder/789/view")


async def test_get_list_views(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"views": [VIEW], "required_views": {}})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_list_views(GetListViewsInput(list_id="901"))

    assert "Sprint Board" in result
    assert fake.calls[0][:2] == ("GET", "/list/901/view")


async def test_get_team_views_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/team/999/view")
    response = httpx.Response(404, request=request, json={"err": "Team not found", "ECODE": "TEAM_001"})
    fake = _FakeClient(exc=httpx.HTTPStatusError("not found", request=request, response=response))
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_team_views(GetTeamViewsInput(team_id="999"))

    assert result.startswith("Error (404)")
    assert "Team not found" in result


# --- single-view tools -------------------------------------------------------


async def test_get_view_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_view(GetViewInput(view_id="abc123"))

    assert "View: Sprint Board" in result
    assert "abc123" in result
    assert '"field": "status"' in result
    assert fake.calls[0][:2] == ("GET", "/view/abc123")


async def test_get_view_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_view(GetViewInput(view_id="abc123", response_format=ResponseFormat.JSON))

    assert '"id": "abc123"' in result


async def test_get_view_tasks_more_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    task = {"id": "t1", "name": "Do the thing", "status": {"status": "in progress"}}
    fake = _FakeClient(payload={"tasks": [task], "last_page": False})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_view_tasks(GetViewTasksInput(view_id="abc123", page=0))

    assert "Do the thing" in result
    assert "More available — call again with page=1" in result
    method, path, kwargs = fake.calls[0]
    assert method == "GET"
    assert path == "/view/abc123/task"
    assert kwargs["params"] == {"page": 0}


async def test_get_view_tasks_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tasks": [], "last_page": True})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_get_view_tasks(GetViewTasksInput(view_id="abc123", page=3))

    assert "No tasks visible" in result
    assert "More available" not in result


async def test_update_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"view": VIEW})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_update_view(
        UpdateViewInput(
            view_id="abc123",
            name="Sprint Board",
            type="board",
            parent_id="901",
            parent_type=6,
            grouping={"field": "assignee"},
        )
    )

    assert "Updated view" in result
    assert "abc123" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert path == "/view/abc123"
    body = kwargs["json_body"]
    assert body["parent"] == {"id": "901", "type": 6}
    assert body["grouping"] == {"field": "assignee"}


async def test_delete_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_delete_view(DeleteViewInput(view_id="abc123"))

    assert "Deleted view id `abc123`" in result
    assert fake.calls[0][:2] == ("DELETE", "/view/abc123")


async def test_delete_view_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("DELETE", "https://api.clickup.com/api/v2/view/missing")
    response = httpx.Response(404, request=request, json={"err": "View not found"})
    fake = _FakeClient(exc=httpx.HTTPStatusError("not found", request=request, response=response))
    monkeypatch.setattr("clickup_mcp.tools.views.get_client", lambda: fake)

    result = await clickup_delete_view(DeleteViewInput(view_id="missing"))

    assert result.startswith("Error (404)")


# --- live smoke (read-only, skipped without creds) --------------------------


@pytest.mark.live
async def test_live_get_team_views() -> None:
    """Smoke-test workspace-level view listing against a real ClickUp Workspace."""
    team_id = os.environ.get("CLICKUP_TEAM_ID")
    assert team_id, "CLICKUP_TEAM_ID must be set in the environment/.env for live tests"

    result = await clickup_get_team_views(GetTeamViewsInput(team_id=team_id))

    assert not result.startswith("Error")
