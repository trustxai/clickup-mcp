"""Unit tests for the space-tag tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.tags import (
    AddTagToTaskInput,
    CreateSpaceTagInput,
    DeleteSpaceTagInput,
    EditSpaceTagInput,
    GetSpaceTagsInput,
    RemoveTagFromTaskInput,
    clickup_add_tag_to_task,
    clickup_create_space_tag,
    clickup_delete_space_tag,
    clickup_edit_space_tag,
    clickup_get_space_tags,
    clickup_remove_tag_from_task,
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


def _not_found_error(path: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://api.clickup.com/api/v2{path}")
    response = httpx.Response(404, json={"err": "Space not found", "ECODE": "SPACE_004"}, request=request)
    return httpx.HTTPStatusError("Not Found", request=request, response=response)


# --- get_space_tags ---------------------------------------------------


async def test_get_space_tags_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tags": [{"name": "urgent", "tag_fg": "#FFFFFF", "tag_bg": "#FF0000"}]})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_get_space_tags(GetSpaceTagsInput(space_id="sp1"))

    assert "urgent" in result
    assert "#FF0000" in result
    assert fake.calls == [("GET", "/space/sp1/tag", {})]


async def test_get_space_tags_json_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tags": []})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_get_space_tags(GetSpaceTagsInput(space_id="sp1", response_format=ResponseFormat.JSON))

    assert '"count": 0' in result
    assert '"tags": []' in result


async def test_get_space_tags_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_not_found_error("/space/missing/tag"))
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_get_space_tags(GetSpaceTagsInput(space_id="missing"))

    assert result.startswith("Error")
    assert "404" in result


# --- create_space_tag ---------------------------------------------------


async def test_create_space_tag_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tag": {"name": "urgent"}})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_create_space_tag(
        CreateSpaceTagInput(space_id="sp1", name="urgent", tag_fg="#FFFFFF", tag_bg="#FF0000")
    )

    assert "Created tag" in result
    assert "urgent" in result
    assert fake.calls == [
        (
            "POST",
            "/space/sp1/tag",
            {"json_body": {"tag": {"name": "urgent", "tag_fg": "#FFFFFF", "tag_bg": "#FF0000"}}},
        )
    ]


async def test_create_space_tag_no_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    await clickup_create_space_tag(CreateSpaceTagInput(space_id="sp1", name="plain"))

    assert fake.calls[0][2]["json_body"] == {"tag": {"name": "plain"}}


def test_create_space_tag_invalid_color() -> None:
    with pytest.raises(ValidationError):
        CreateSpaceTagInput(space_id="sp1", name="urgent", tag_fg="red")


# --- edit_space_tag ---------------------------------------------------


async def test_edit_space_tag_rename_and_recolor(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_edit_space_tag(
        EditSpaceTagInput(space_id="sp1", tag_name="urgent", new_name="critical", tag_bg="#990000")
    )

    assert "critical" in result
    assert fake.calls == [
        (
            "PUT",
            "/space/sp1/tag/urgent",
            {"json_body": {"tag": {"name": "critical", "tag_bg": "#990000"}}},
        )
    ]


async def test_edit_space_tag_url_encodes_name(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    await clickup_edit_space_tag(EditSpaceTagInput(space_id="sp1", tag_name="in progress"))

    method, path, _ = fake.calls[0]
    assert method == "PUT"
    assert path == "/space/sp1/tag/in%20progress"


# --- delete_space_tag ---------------------------------------------------


async def test_delete_space_tag_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_delete_space_tag(DeleteSpaceTagInput(space_id="sp1", tag_name="urgent"))

    assert "Deleted tag" in result
    assert fake.calls == [
        ("DELETE", "/space/sp1/tag/urgent", {"json_body": {"tag": {"name": "urgent"}}}),
    ]


async def test_delete_space_tag_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_not_found_error("/space/sp1/tag/missing"))
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_delete_space_tag(DeleteSpaceTagInput(space_id="sp1", tag_name="missing"))

    assert result.startswith("Error")
    assert "404" in result


# --- add_tag_to_task ---------------------------------------------------


async def test_add_tag_to_task_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_add_tag_to_task(AddTagToTaskInput(task_id="9hz", tag_name="urgent"))

    assert "Added tag" in result
    assert fake.calls == [("POST", "/task/9hz/tag/urgent", {"params": None})]


async def test_add_tag_to_task_custom_task_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    params = AddTagToTaskInput(task_id="CUST-1", tag_name="urgent", custom_task_ids=True, team_id="123")
    await clickup_add_tag_to_task(params)

    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/task/CUST-1/tag/urgent"
    assert kwargs["params"] == {"custom_task_ids": "true", "team_id": "123"}


# --- remove_tag_from_task ---------------------------------------------------


async def test_remove_tag_from_task_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_remove_tag_from_task(RemoveTagFromTaskInput(task_id="9hz", tag_name="urgent"))

    assert "Removed tag" in result
    assert fake.calls == [("DELETE", "/task/9hz/tag/urgent", {"params": None})]


async def test_remove_tag_from_task_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_not_found_error("/task/9hz/tag/missing"))
    monkeypatch.setattr("clickup_mcp.tools.tags.get_client", lambda: fake)

    result = await clickup_remove_tag_from_task(RemoveTagFromTaskInput(task_id="9hz", tag_name="missing"))

    assert result.startswith("Error")
    assert "404" in result
