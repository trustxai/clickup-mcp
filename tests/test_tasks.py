"""Unit tests for the Tasks tool module against a fake client.

Covers a happy path per tool, an error path, and pagination/edge cases. All
requests are intercepted by `_FakeClient`, which records the exact
(method, path, params, json_body, use_v3) so tests assert on both the returned
string and the outgoing request.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.tasks import (
    CreateTaskFromTemplateInput,
    CreateTaskInput,
    DeleteTaskInput,
    GetBulkTasksTimeInStatusInput,
    GetFilteredTeamTasksInput,
    GetTaskInput,
    GetTasksInput,
    GetTaskTemplatesInput,
    GetTaskTimeInStatusInput,
    MergeTasksInput,
    MoveTaskInput,
    UpdateTaskInput,
    clickup_create_task,
    clickup_create_task_from_template,
    clickup_delete_task,
    clickup_get_bulk_tasks_time_in_status,
    clickup_get_filtered_team_tasks,
    clickup_get_task,
    clickup_get_task_templates,
    clickup_get_task_time_in_status,
    clickup_get_tasks,
    clickup_merge_tasks,
    clickup_move_task,
    clickup_update_task,
)


class _FakeResponse:
    def __init__(self, payload: Any = None, content: bytes = b"{}") -> None:
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        use_v3: bool = False,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, "params": params, "json_body": json_body, "use_v3": use_v3})
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.tasks.get_client", lambda: fake)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/task/x")
    response = httpx.Response(status, json={"err": "Not found", "ECODE": "TASK_001"}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# --------------------------------------------------------------------------- #
# create_task
# --------------------------------------------------------------------------- #
async def test_create_task_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "86new", "name": "Ship v2", "url": "https://app/86new"})
    _patch_client(monkeypatch, fake)

    result = await clickup_create_task(
        CreateTaskInput(
            list_id="901",
            name="Ship v2",
            markdown_content="## Goal",
            assignees=[123],
            priority=2,
            custom_fields=[{"id": "abc", "value": "x"}],
        )
    )

    assert "Created task" in result and "86new" in result
    call = fake.last
    assert call["method"] == "POST"
    assert call["path"] == "/list/901/task"
    body = call["json_body"]
    assert body["name"] == "Ship v2"
    assert body["markdown_content"] == "## Goal"
    assert body["assignees"] == [123]
    assert body["priority"] == 2
    assert body["custom_fields"] == [{"id": "abc", "value": "x"}]
    # Omitted optional fields must not appear in the body.
    assert "description" not in body
    assert "tags" not in body


async def test_create_task_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404))
    _patch_client(monkeypatch, fake)

    result = await clickup_create_task(CreateTaskInput(list_id="bad", name="x"))

    assert result.startswith("Error (404)")
    assert "TASK_001" in result


# --------------------------------------------------------------------------- #
# get_task
# --------------------------------------------------------------------------- #
async def test_get_task_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "id": "86cxy1",
            "name": "Draft spec",
            "status": {"status": "in progress"},
            "assignees": [{"id": 1, "username": "Alejandro"}],
            "url": "https://app/86cxy1",
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_get_task(GetTaskInput(task_id="86cxy1", include_subtasks=True))

    assert "Draft spec" in result and "in progress" in result and "Alejandro" in result
    call = fake.last
    assert call["method"] == "GET"
    assert call["path"] == "/task/86cxy1"
    assert call["params"]["include_subtasks"] is True
    assert "custom_task_ids" not in call["params"]


async def test_get_task_custom_id_query(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "PROJ-42", "name": "T"})
    _patch_client(monkeypatch, fake)

    await clickup_get_task(GetTaskInput(task_id="PROJ-42", custom_task_ids=True, team_id="9000"))

    params = fake.last["params"]
    assert params["custom_task_ids"] is True
    assert params["team_id"] == "9000"


async def test_get_task_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "86cxy1", "name": "T"})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_task(GetTaskInput(task_id="86cxy1", response_format="json"))

    assert json.loads(result)["id"] == "86cxy1"


# --------------------------------------------------------------------------- #
# get_tasks
# --------------------------------------------------------------------------- #
async def test_get_tasks_filters_and_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "tasks": [{"id": "1", "name": "A", "status": {"status": "open"}}],
            "last_page": False,
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_get_tasks(
        GetTasksInput(list_id="901", statuses=["in progress"], order_by="due_date", page=0)
    )

    assert "Tasks in list 901" in result
    assert "More available" in result  # last_page is False
    params = fake.last["params"]
    assert params["page"] == 0
    assert params["order_by"] == "due_date"
    # Array filters must use ClickUp's bracket notation.
    assert params["statuses[]"] == ["in progress"]
    assert fake.last["path"] == "/list/901/task"


async def test_get_tasks_custom_fields_json_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tasks": [], "last_page": True})
    _patch_client(monkeypatch, fake)

    await clickup_get_tasks(
        GetTasksInput(list_id="901", custom_fields=[{"field_id": "u1", "operator": "=", "value": "x"}])
    )

    raw = fake.last["params"]["custom_fields"]
    assert isinstance(raw, str)
    assert json.loads(raw) == [{"field_id": "u1", "operator": "=", "value": "x"}]


# --------------------------------------------------------------------------- #
# update_task
# --------------------------------------------------------------------------- #
async def test_update_task_assignees_add_rem(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_update_task(
        UpdateTaskInput(task_id="86cxy1", name="New title", add_assignees=[123], remove_assignees=[456])
    )

    assert "Updated task" in result
    call = fake.last
    assert call["method"] == "PUT"
    assert call["path"] == "/task/86cxy1"
    assert call["json_body"]["name"] == "New title"
    assert call["json_body"]["assignees"] == {"add": [123], "rem": [456]}


async def test_update_task_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_update_task(UpdateTaskInput(task_id="86cxy1"))

    assert result.startswith("Error")
    assert "no fields to update" in result
    assert fake.calls == []  # never hit the API


# --------------------------------------------------------------------------- #
# delete_task
# --------------------------------------------------------------------------- #
async def test_delete_task(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_delete_task(DeleteTaskInput(task_id="86cxy1"))

    assert "Deleted task" in result
    assert fake.last["method"] == "DELETE"
    assert fake.last["path"] == "/task/86cxy1"


# --------------------------------------------------------------------------- #
# get_filtered_team_tasks
# --------------------------------------------------------------------------- #
async def test_get_filtered_team_tasks_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tasks": [{"id": "1", "name": "A"}], "last_page": True})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_filtered_team_tasks(
        GetFilteredTeamTasksInput(team_id="9000", space_ids=["10", "11"], assignees=["123"])
    )

    assert "Workspace tasks (team 9000)" in result
    call = fake.last
    assert call["path"] == "/team/9000/task"
    assert call["params"]["space_ids[]"] == ["10", "11"]
    assert call["params"]["assignees[]"] == ["123"]


async def test_get_filtered_team_tasks_requires_team_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tasks": []})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_filtered_team_tasks(GetFilteredTeamTasksInput())

    assert result.startswith("Error")
    assert "team_id is required" in result
    assert fake.calls == []


async def test_get_filtered_team_tasks_team_id_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tasks": [], "last_page": True})
    _patch_client(monkeypatch, fake)

    class _Settings:
        clickup_team_id = "settings-team"

    monkeypatch.setattr("clickup_mcp.tools.tasks.get_settings", lambda: _Settings())

    await clickup_get_filtered_team_tasks(GetFilteredTeamTasksInput())

    assert fake.last["path"] == "/team/settings-team/task"


# --------------------------------------------------------------------------- #
# move_task (v3)
# --------------------------------------------------------------------------- #
async def test_move_task_uses_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_move_task(
        MoveTaskInput(
            task_id="86cxy1",
            list_id="902",
            workspace_id="9000",
            status_mappings=[{"from": "to do", "to": "open"}],
        )
    )

    assert "Moved task" in result
    call = fake.last
    assert call["use_v3"] is True
    assert call["method"] == "PUT"
    assert call["path"] == "/workspaces/9000/tasks/86cxy1/home_list/902"
    assert call["json_body"]["status_mappings"] == [{"from": "to do", "to": "open"}]


# --------------------------------------------------------------------------- #
# merge_tasks
# --------------------------------------------------------------------------- #
async def test_merge_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_merge_tasks(MergeTasksInput(task_id="86target", source_task_ids=["86a", "86b"]))

    assert "Merged" in result and "86target" in result
    call = fake.last
    assert call["method"] == "POST"
    assert call["path"] == "/task/86target/merge"
    assert call["json_body"] == {"source_task_ids": ["86a", "86b"]}


# --------------------------------------------------------------------------- #
# create_task_from_template
# --------------------------------------------------------------------------- #
async def test_create_task_from_template(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "86tpl", "name": "Onboard Acme"})
    _patch_client(monkeypatch, fake)

    result = await clickup_create_task_from_template(
        CreateTaskFromTemplateInput(list_id="901", template_id="t-123", name="Onboard Acme")
    )

    assert "Created task" in result and "86tpl" in result
    call = fake.last
    assert call["method"] == "POST"
    assert call["path"] == "/list/901/taskTemplate/t-123"
    assert call["json_body"] == {"name": "Onboard Acme"}


# --------------------------------------------------------------------------- #
# get_task_templates
# --------------------------------------------------------------------------- #
async def test_get_task_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"templates": [{"id": "t-1", "name": "Bug"}]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_task_templates(GetTaskTemplatesInput(team_id="9000", page=0))

    assert "Bug" in result and "t-1" in result
    call = fake.last
    assert call["path"] == "/team/9000/taskTemplate"
    assert call["params"] == {"page": 0}


# --------------------------------------------------------------------------- #
# time in status
# --------------------------------------------------------------------------- #
async def test_get_task_time_in_status_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "current_status": {"status": "in progress", "total_time": {"by_minute": 90}},
            "status_history": [{"status": "open", "total_time": {"by_minute": 30}}],
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_get_task_time_in_status(GetTaskTimeInStatusInput(task_id="86cxy1"))

    assert "Current status:** in progress (1h 30m)" in result
    assert "open: 30m" in result
    assert fake.last["path"] == "/task/86cxy1/time_in_status"


async def test_get_bulk_tasks_time_in_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "86a": {"current_status": {"status": "open", "total_time": {"by_minute": 10}}, "status_history": []},
            "86b": {"current_status": {"status": "done", "total_time": {"by_minute": 5}}, "status_history": []},
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_get_bulk_tasks_time_in_status(GetBulkTasksTimeInStatusInput(task_ids=["86a", "86b"]))

    assert "Task 86a" in result and "Task 86b" in result
    call = fake.last
    assert call["path"] == "/task/bulk_time_in_status/task_ids"
    assert call["params"]["task_ids"] == ["86a", "86b"]


def test_get_bulk_tasks_time_in_status_rejects_single_id() -> None:
    with pytest.raises(ValidationError):
        GetBulkTasksTimeInStatusInput(task_ids=["86a"])
