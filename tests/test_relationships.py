"""Unit tests for the task relationship tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.relationships import (
    AddDependencyInput,
    DeleteDependencyInput,
    TaskLinkInput,
    clickup_add_dependency,
    clickup_add_task_link,
    clickup_delete_dependency,
    clickup_delete_task_link,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.relationships.get_client", lambda: fake)


async def test_add_dependency_depends_on(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    _patch(monkeypatch, fake)

    result = await clickup_add_dependency(AddDependencyInput(task_id="abc", depends_on="xyz"))

    assert "depends on task **xyz**" in result
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/task/abc/dependency"
    assert call["json_body"] == {"depends_on": "xyz"}
    assert call["params"] is None


async def test_add_dependency_dependency_of_with_custom_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    _patch(monkeypatch, fake)

    result = await clickup_add_dependency(
        AddDependencyInput(task_id="PROJ-1", dependency_of="PROJ-2", custom_task_ids=True, team_id="123")
    )

    assert "**PROJ-2** now depends on task **PROJ-1**" in result
    call = fake.calls[0]
    assert call["json_body"] == {"dependency_of": "PROJ-2"}
    assert call["params"] == {"custom_task_ids": "true", "team_id": "123"}


async def test_add_dependency_requires_exactly_one_direction() -> None:
    with pytest.raises(ValidationError):
        AddDependencyInput(task_id="abc")  # neither direction
    with pytest.raises(ValidationError):
        AddDependencyInput(task_id="abc", depends_on="x", dependency_of="y")  # both


async def test_custom_task_ids_requires_team_id() -> None:
    with pytest.raises(ValidationError):
        AddDependencyInput(task_id="abc", depends_on="xyz", custom_task_ids=True)


async def test_delete_dependency_puts_direction_in_query(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    _patch(monkeypatch, fake)

    result = await clickup_delete_dependency(DeleteDependencyInput(task_id="abc", depends_on="xyz"))

    assert "Removed dependency" in result
    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == "/task/abc/dependency"
    assert call["params"] == {"depends_on": "xyz"}


async def test_delete_dependency_custom_ids_merged_into_query(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    _patch(monkeypatch, fake)

    await clickup_delete_dependency(
        DeleteDependencyInput(task_id="abc", dependency_of="xyz", custom_task_ids=True, team_id="9")
    )

    assert fake.calls[0]["params"] == {"dependency_of": "xyz", "custom_task_ids": "true", "team_id": "9"}


async def test_add_task_link(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    _patch(monkeypatch, fake)

    result = await clickup_add_task_link(TaskLinkInput(task_id="abc", links_to="xyz"))

    assert "Linked task **abc** to task **xyz**" in result
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/task/abc/link/xyz"
    assert call["params"] is None


async def test_delete_task_link(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    _patch(monkeypatch, fake)

    result = await clickup_delete_task_link(TaskLinkInput(task_id="abc", links_to="xyz"))

    assert "Removed link between task **abc** and task **xyz**" in result
    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == "/task/abc/link/xyz"


async def test_add_dependency_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://api.clickup.com/api/v2/task/abc/dependency")
    response = httpx.Response(403, json={"err": "Dependencies disabled", "ECODE": "OAUTH_027"}, request=request)
    fake = _FakeClient(exc=httpx.HTTPStatusError("403", request=request, response=response))
    _patch(monkeypatch, fake)

    result = await clickup_add_dependency(AddDependencyInput(task_id="abc", depends_on="xyz"))

    assert result.startswith("Error (403)")
    assert "Dependencies disabled" in result
