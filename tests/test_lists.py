"""Unit tests for the Lists tool module against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from clickup_mcp.tools.lists import (
    AddTaskToListInput,
    CreateFolderlessListInput,
    CreateListFromTemplateInFolderInput,
    CreateListFromTemplateInSpaceInput,
    CreateListInput,
    DeleteListInput,
    GetFolderlessListsInput,
    GetListInput,
    GetListsInput,
    GetListTemplatesInput,
    RemoveTaskFromListInput,
    UpdateListInput,
    clickup_add_task_to_list,
    clickup_create_folderless_list,
    clickup_create_list,
    clickup_create_list_from_template_in_folder,
    clickup_create_list_from_template_in_space,
    clickup_delete_list,
    clickup_get_folderless_lists,
    clickup_get_list,
    clickup_get_list_templates,
    clickup_get_lists,
    clickup_remove_task_from_list,
    clickup_update_list,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Records every call as (method, path, params, json_body) and replays canned responses."""

    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs.get("params"), kwargs.get("json_body")))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _mock_get_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.lists.get_client", lambda: fake)


# --------------------------------------------------------------------------
# clickup_create_list
# --------------------------------------------------------------------------


async def test_create_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "999", "name": "Sprint 24"})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_create_list(CreateListInput(folder_id="12345", name="Sprint 24", priority=2))

    assert "OK" in result
    assert "Sprint 24" in result
    assert "999" in result
    method, path, _params, body = fake.calls[0]
    assert (method, path) == ("POST", "/folder/12345/list")
    assert body == {"name": "Sprint 24", "priority": 2}


async def test_create_list_error(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = httpx.Response(404, json={"err": "Folder not found", "ECODE": "FOLD_001"})
    exc = httpx.HTTPStatusError("not found", request=httpx.Request("POST", "https://x"), response=resp)
    fake = _FakeClient(exc=exc)
    _mock_get_client(monkeypatch, fake)

    result = await clickup_create_list(CreateListInput(folder_id="bad", name="X"))

    assert result.startswith("Error (404)")
    assert "Folder not found" in result


# --------------------------------------------------------------------------
# clickup_create_folderless_list
# --------------------------------------------------------------------------


async def test_create_folderless_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "111", "name": "General"})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_create_folderless_list(CreateFolderlessListInput(space_id="67890", name="General"))

    assert "OK" in result
    assert "General" in result
    method, path, _params, body = fake.calls[0]
    assert (method, path) == ("POST", "/space/67890/list")
    assert body == {"name": "General"}


# --------------------------------------------------------------------------
# clickup_get_lists (+ pagination edge case)
# --------------------------------------------------------------------------


async def test_get_lists_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "lists": [
                {"id": "1", "name": "A", "task_count": 3},
                {"id": "2", "name": "B", "task_count": 0},
            ]
        }
    )
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_lists(GetListsInput(folder_id="12345"))

    assert "Lists in Folder 12345" in result
    assert "**A**" in result
    assert "**B**" in result
    method, path, params, _body = fake.calls[0]
    assert (method, path) == ("GET", "/folder/12345/list")
    assert params == {"archived": False}


async def test_get_lists_pagination_windowing(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [{"id": str(i), "name": f"List {i}"} for i in range(5)]
    fake = _FakeClient(payload={"lists": items})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_lists(GetListsInput(folder_id="12345", limit=2, offset=2))

    assert "List 2" in result
    assert "List 3" in result
    assert "List 0" not in result
    assert "List 4" not in result
    assert "More available" in result
    assert "next offset" in result


async def test_get_lists_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"lists": []})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_lists(GetListsInput(folder_id="12345"))

    assert "No items" in result


# --------------------------------------------------------------------------
# clickup_get_folderless_lists
# --------------------------------------------------------------------------


async def test_get_folderless_lists_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"lists": [{"id": "1", "name": "General"}]})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_folderless_lists(GetFolderlessListsInput(space_id="67890", archived=True))

    assert "Folderless Lists in Space 67890" in result
    method, path, params, _body = fake.calls[0]
    assert (method, path) == ("GET", "/space/67890/list")
    assert params == {"archived": True}


# --------------------------------------------------------------------------
# clickup_get_list
# --------------------------------------------------------------------------


async def test_get_list_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "id": "42",
            "name": "Sprint 24",
            "content": "Sprint scope",
            "folder": {"id": "f1", "name": "Engineering"},
            "space": {"id": "s1", "name": "Product"},
            "status": {"status": "blue", "color": "#0000ff"},
            "priority": {"priority": "2"},
            "assignee": {"username": "alej"},
            "archived": False,
            "task_count": 7,
        }
    )
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_list(GetListInput(list_id="42"))

    assert "Sprint 24" in result
    assert "Engineering" in result
    assert "Product" in result
    assert "blue" in result
    assert "alej" in result
    assert "Sprint scope" in result


async def test_get_list_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "42", "name": "Sprint 24"})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_list(GetListInput(list_id="42", response_format="json"))

    assert '"id": "42"' in result
    assert '"name": "Sprint 24"' in result


# --------------------------------------------------------------------------
# clickup_update_list
# --------------------------------------------------------------------------


async def test_update_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"list": {"id": "42", "name": "Sprint 25"}})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_update_list(UpdateListInput(list_id="42", name="Sprint 25"))

    assert "OK" in result
    assert "Sprint 25" in result
    method, path, _params, body = fake.calls[0]
    assert (method, path) == ("PUT", "/list/42")
    assert body == {"name": "Sprint 25"}


async def test_update_list_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_update_list(UpdateListInput(list_id="42"))

    assert result.startswith("Error")
    assert fake.calls == []


async def test_update_list_unset_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "42", "name": "Sprint 25"})
    _mock_get_client(monkeypatch, fake)

    await clickup_update_list(UpdateListInput(list_id="42", unset_status=True))

    _method, _path, _params, body = fake.calls[0]
    assert body == {"unset_status": True}


# --------------------------------------------------------------------------
# clickup_delete_list
# --------------------------------------------------------------------------


async def test_delete_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_delete_list(DeleteListInput(list_id="42"))

    assert "OK" in result
    assert "42" in result
    assert fake.calls[0][:2] == ("DELETE", "/list/42")


# --------------------------------------------------------------------------
# clickup_add_task_to_list / clickup_remove_task_from_list
# --------------------------------------------------------------------------


async def test_add_task_to_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_add_task_to_list(AddTaskToListInput(list_id="42", task_id="abc123"))

    assert "OK" in result
    assert fake.calls[0][:2] == ("POST", "/list/42/task/abc123")


async def test_add_task_to_list_clickapp_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = httpx.Response(403, json={"err": "ClickApp disabled", "ECODE": "TASK_050"})
    exc = httpx.HTTPStatusError("forbidden", request=httpx.Request("POST", "https://x"), response=resp)
    fake = _FakeClient(exc=exc)
    _mock_get_client(monkeypatch, fake)

    result = await clickup_add_task_to_list(AddTaskToListInput(list_id="42", task_id="abc123"))

    assert result.startswith("Error (403)")
    assert "ClickApp disabled" in result


async def test_remove_task_from_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_remove_task_from_list(RemoveTaskFromListInput(list_id="42", task_id="abc123"))

    assert "OK" in result
    assert fake.calls[0][:2] == ("DELETE", "/list/42/task/abc123")


# --------------------------------------------------------------------------
# clickup_create_list_from_template_in_folder / _in_space
# --------------------------------------------------------------------------


async def test_create_list_from_template_in_folder_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "777"})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_create_list_from_template_in_folder(
        CreateListFromTemplateInFolderInput(folder_id="12345", template_id="t-1", name="Sprint 24")
    )

    assert "OK" in result
    assert "777" in result
    method, path, _params, body = fake.calls[0]
    assert (method, path) == ("POST", "/folder/12345/list_template/t-1")
    assert body == {"name": "Sprint 24", "options": {"return_immediately": True}}


async def test_create_list_from_template_in_folder_with_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "777"})
    _mock_get_client(monkeypatch, fake)

    await clickup_create_list_from_template_in_folder(
        CreateListFromTemplateInFolderInput(
            folder_id="12345",
            template_id="t-1",
            name="Sprint 24",
            return_immediately=False,
            options={"content": "Scope"},
        )
    )

    _method, _path, _params, body = fake.calls[0]
    assert body == {"name": "Sprint 24", "options": {"return_immediately": False, "content": "Scope"}}


async def test_create_list_from_template_in_space_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "888"})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_create_list_from_template_in_space(
        CreateListFromTemplateInSpaceInput(space_id="67890", template_id="t-2", name="General")
    )

    assert "OK" in result
    assert "888" in result
    method, path, _params, body = fake.calls[0]
    assert (method, path) == ("POST", "/space/67890/list_template/t-2")
    assert body == {"name": "General", "options": {"return_immediately": True}}


# --------------------------------------------------------------------------
# clickup_get_list_templates
# --------------------------------------------------------------------------


async def test_get_list_templates_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"templates": [{"id": "t-1", "name": "Sprint Template"}]})
    _mock_get_client(monkeypatch, fake)

    result = await clickup_get_list_templates(GetListTemplatesInput(team_id="90130012345"))

    assert "Sprint Template" in result
    method, path, _params, _body = fake.calls[0]
    assert (method, path) == ("GET", "/team/90130012345/list_template")


async def test_get_list_templates_falls_back_to_settings_team_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"templates": []})
    _mock_get_client(monkeypatch, fake)

    class _FakeSettings:
        clickup_team_id = "default-team"

    monkeypatch.setattr("clickup_mcp.tools.lists.get_settings", lambda: _FakeSettings())

    await clickup_get_list_templates(GetListTemplatesInput())

    method, path, _params, _body = fake.calls[0]
    assert (method, path) == ("GET", "/team/default-team/list_template")


async def test_get_list_templates_missing_team_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"templates": []})
    _mock_get_client(monkeypatch, fake)

    class _FakeSettings:
        clickup_team_id = ""

    monkeypatch.setattr("clickup_mcp.tools.lists.get_settings", lambda: _FakeSettings())

    result = await clickup_get_list_templates(GetListTemplatesInput())

    assert result.startswith("Error")
    assert "team_id" in result
    assert fake.calls == []


# --------------------------------------------------------------------------
# input model validation
# --------------------------------------------------------------------------


def test_create_list_input_forbids_extra_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        CreateListInput(folder_id="1", name="X", bogus="nope")  # type: ignore[call-arg]


def test_update_list_input_rejects_bad_priority() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        UpdateListInput(list_id="1", priority=9)


@pytest.mark.live
async def test_live_smoke_placeholder() -> None:
    """Placeholder live smoke test — skipped automatically without CLICKUP_API_TOKEN.

    A full live smoke run (create/get/update/delete against a sandbox
    Workspace) is owned by t19-live-smoke; this only exercises the read-only
    get_lists path against a real team, gated behind CLICKUP_TEAM_ID.
    """
    from clickup_mcp.config import get_settings

    settings = get_settings()
    if not settings.clickup_team_id:
        pytest.skip("CLICKUP_TEAM_ID not configured for live smoke")
    result = await clickup_get_list_templates(GetListTemplatesInput(team_id=settings.clickup_team_id))
    assert "Error" not in result or "not found" in result.lower()
