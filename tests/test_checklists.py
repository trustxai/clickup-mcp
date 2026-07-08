"""Unit tests for the checklists tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.checklists import (
    CreateChecklistInput,
    CreateChecklistItemInput,
    DeleteChecklistInput,
    DeleteChecklistItemInput,
    EditChecklistInput,
    EditChecklistItemInput,
    clickup_create_checklist,
    clickup_create_checklist_item,
    clickup_delete_checklist,
    clickup_delete_checklist_item,
    clickup_edit_checklist,
    clickup_edit_checklist_item,
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
    response = httpx.Response(404, json={"err": "Checklist not found", "ECODE": "ITEM_004"}, request=request)
    return httpx.HTTPStatusError("Not Found", request=request, response=response)


# --- create_checklist ---------------------------------------------------


async def test_create_checklist_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"checklist": {"id": "cl1", "name": "Launch", "items": []}})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_create_checklist(CreateChecklistInput(task_id="9hz", name="Launch"))

    assert "Created checklist" in result
    assert "Launch" in result
    assert "cl1" in result
    assert fake.calls == [("POST", "/task/9hz/checklist", {"params": None, "json_body": {"name": "Launch"}})]


async def test_create_checklist_custom_task_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"checklist": {"id": "cl2", "name": "QA"}})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    params = CreateChecklistInput(task_id="CUST-1", name="QA", custom_task_ids=True, team_id="123")
    await clickup_create_checklist(params)

    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/task/CUST-1/checklist"
    assert kwargs["params"] == {"custom_task_ids": "true", "team_id": "123"}


async def test_create_checklist_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_not_found_error("/task/missing/checklist"))
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_create_checklist(CreateChecklistInput(task_id="missing", name="X"))

    assert result.startswith("Error")
    assert "404" in result


# --- edit_checklist ------------------------------------------------------


async def test_edit_checklist_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"checklist": {"id": "cl1", "name": "Renamed"}})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_edit_checklist(EditChecklistInput(checklist_id="cl1", name="Renamed"))

    assert "Updated checklist" in result
    assert "Renamed" in result
    assert fake.calls == [("PUT", "/checklist/cl1", {"json_body": {"name": "Renamed"}})]


async def test_edit_checklist_position_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"checklist": {"id": "cl1"}})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    await clickup_edit_checklist(EditChecklistInput(checklist_id="cl1", position=0))

    assert fake.calls[0][2]["json_body"] == {"position": 0}


def test_edit_checklist_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        EditChecklistInput(checklist_id="cl1")


# --- delete_checklist ------------------------------------------------------


async def test_delete_checklist_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_delete_checklist(DeleteChecklistInput(checklist_id="cl1"))

    assert "Deleted checklist" in result
    assert "cl1" in result
    assert fake.calls == [("DELETE", "/checklist/cl1", {})]


# --- create_checklist_item ------------------------------------------------


async def test_create_checklist_item_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={"checklist": {"id": "cl1", "items": [{"id": "it1", "name": "Write tests", "resolved": False}]}}
    )
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_create_checklist_item(CreateChecklistItemInput(checklist_id="cl1", name="Write tests"))

    assert "Added item" in result
    assert "Write tests" in result
    assert "it1" in result
    assert fake.calls == [
        ("POST", "/checklist/cl1/checklist_item", {"json_body": {"name": "Write tests"}}),
    ]


async def test_create_checklist_item_with_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"checklist": {"id": "cl1", "items": []}})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    await clickup_create_checklist_item(CreateChecklistItemInput(checklist_id="cl1", name="Review", assignee=183))

    assert fake.calls[0][2]["json_body"] == {"name": "Review", "assignee": 183}


# --- edit_checklist_item ---------------------------------------------------


async def test_edit_checklist_item_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={"checklist": {"id": "cl1", "items": [{"id": "it1", "name": "Write tests", "resolved": True}]}}
    )
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_edit_checklist_item(
        EditChecklistItemInput(checklist_id="cl1", checklist_item_id="it1", resolved=True)
    )

    assert "Updated checklist item" in result
    assert "resolved" in result
    assert fake.calls == [
        ("PUT", "/checklist/cl1/checklist_item/it1", {"json_body": {"resolved": True}}),
    ]


async def test_edit_checklist_item_clear_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    await clickup_edit_checklist_item(
        EditChecklistItemInput(checklist_id="cl1", checklist_item_id="it1", clear_assignee=True)
    )

    assert fake.calls[0][2]["json_body"] == {"assignee": None}


async def test_edit_checklist_item_nest_under_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    await clickup_edit_checklist_item(EditChecklistItemInput(checklist_id="cl1", checklist_item_id="it1", parent="it0"))

    assert fake.calls[0][2]["json_body"] == {"parent": "it0"}


def test_edit_checklist_item_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        EditChecklistItemInput(checklist_id="cl1", checklist_item_id="it1")


def test_edit_checklist_item_conflicting_assignee_args() -> None:
    with pytest.raises(ValidationError):
        EditChecklistItemInput(checklist_id="cl1", checklist_item_id="it1", assignee=5, clear_assignee=True)


# --- delete_checklist_item --------------------------------------------------


async def test_delete_checklist_item_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_delete_checklist_item(DeleteChecklistItemInput(checklist_id="cl1", checklist_item_id="it1"))

    assert "Deleted checklist item" in result
    assert fake.calls == [("DELETE", "/checklist/cl1/checklist_item/it1", {})]


async def test_delete_checklist_item_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_not_found_error("/checklist/cl1/checklist_item/missing"))
    monkeypatch.setattr("clickup_mcp.tools.checklists.get_client", lambda: fake)

    result = await clickup_delete_checklist_item(
        DeleteChecklistItemInput(checklist_id="cl1", checklist_item_id="missing")
    )

    assert result.startswith("Error")
    assert "404" in result
