"""Unit tests for the Folders tools against a fake client.

Mirrors `tests/test_health.py`'s `_FakeClient` pattern, extended to capture
the request `params`/`json_body` so assertions can cover the exact payload
sent to ClickUp (house standard #7).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.config import get_settings
from clickup_mcp.tools.folders import (
    CreateFolderFromTemplateInput,
    CreateFolderInput,
    DeleteFolderInput,
    GetFolderInput,
    GetFoldersInput,
    GetFolderTemplatesInput,
    UpdateFolderInput,
    clickup_create_folder,
    clickup_create_folder_from_template,
    clickup_delete_folder,
    clickup_get_folder,
    clickup_get_folder_templates,
    clickup_get_folders,
    clickup_update_folder,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.folders.get_client", lambda: fake)


# --------------------------------------------------------------------------
# clickup_create_folder
# --------------------------------------------------------------------------


async def test_create_folder_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "456", "name": "Q3 Launches"})
    _patch_client(monkeypatch, fake)

    result = await clickup_create_folder(
        CreateFolderInput(space_id="90130912", name="Q3 Launches", override_statuses=True)
    )

    assert "Created folder" in result
    assert "Q3 Launches" in result
    assert "456" in result
    assert fake.calls == [
        ("POST", "/space/90130912/folder", {"json_body": {"name": "Q3 Launches", "override_statuses": True}})
    ]


async def test_create_folder_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(400, {"err": "Name is required"}))
    _patch_client(monkeypatch, fake)

    result = await clickup_create_folder(CreateFolderInput(space_id="90130912", name="X"))

    assert result.startswith("Error (400)")


def test_create_folder_input_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CreateFolderInput(space_id="1", name="X", bogus=True)  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# clickup_get_folders
# --------------------------------------------------------------------------


async def test_get_folders_ok_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "folders": [
                {"id": "1", "name": "Alpha", "task_count": "3", "lists": [{"id": "l1"}], "override_statuses": False},
                {"id": "2", "name": "Beta", "task_count": "0", "lists": [], "override_statuses": True},
            ]
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folders(GetFoldersInput(space_id="90130912"))

    assert "Folders in Space 90130912" in result
    assert "Alpha" in result
    assert "Beta" in result
    assert fake.calls == [("GET", "/space/90130912/folder", {"params": {"archived": "false"}})]


async def test_get_folders_pagination_slices_client_side(monkeypatch: pytest.MonkeyPatch) -> None:
    folders = [{"id": str(i), "name": f"Folder {i}"} for i in range(5)]
    fake = _FakeClient(payload={"folders": folders})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folders(GetFoldersInput(space_id="1", limit=2, offset=2))

    assert "Folder 2" in result
    assert "Folder 3" in result
    assert "Folder 0" not in result
    assert "Folder 4" not in result
    assert "total **5**" in result
    assert "next offset → **4**" in result


async def test_get_folders_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"folders": [{"id": "1", "name": "Alpha"}]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folders(GetFoldersInput(space_id="1", response_format="json"))

    assert '"title": "Folders in Space 1"' in result
    assert '"name": "Alpha"' in result


async def test_get_folders_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(404))
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folders(GetFoldersInput(space_id="missing"))

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# clickup_get_folder
# --------------------------------------------------------------------------


async def test_get_folder_ok_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "id": "456",
            "name": "Q3 Launches",
            "space": {"id": "90130912", "name": "Marketing"},
            "override_statuses": True,
            "statuses": [{"status": "to do"}, {"status": "done"}],
            "lists": [{"id": "l1"}],
            "task_count": "3",
            "permission_level": "create",
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder(GetFolderInput(folder_id="456"))

    assert "# Folder: Q3 Launches" in result
    assert "Marketing" in result
    assert "to do, done" in result
    assert fake.calls == [("GET", "/folder/456", {})]


async def test_get_folder_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "456", "name": "Q3 Launches"})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder(GetFolderInput(folder_id="456", response_format="json"))

    assert '"id": "456"' in result
    assert '"name": "Q3 Launches"' in result


async def test_get_folder_no_statuses_inherits_space(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "456", "name": "Empty", "statuses": []})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder(GetFolderInput(folder_id="456"))

    assert "inherits the Space's statuses" in result


async def test_get_folder_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(404))
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder(GetFolderInput(folder_id="missing"))

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# clickup_update_folder
# --------------------------------------------------------------------------


async def test_update_folder_ok_with_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "456", "name": "Renamed"})
    _patch_client(monkeypatch, fake)

    result = await clickup_update_folder(UpdateFolderInput(folder_id="456", name="Renamed", override_statuses=True))

    assert "Renamed" in result
    assert "override_statuses=True" in result
    assert fake.calls == [
        ("PUT", "/folder/456", {"json_body": {"name": "Renamed", "override_statuses": True}}),
    ]


async def test_update_folder_ok_without_override_omits_field(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "456", "name": "Renamed"})
    _patch_client(monkeypatch, fake)

    result = await clickup_update_folder(UpdateFolderInput(folder_id="456", name="Renamed"))

    assert "Renamed" in result
    assert "override_statuses" not in result
    assert fake.calls == [("PUT", "/folder/456", {"json_body": {"name": "Renamed"}})]


async def test_update_folder_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(400, {"err": "Invalid name"}))
    _patch_client(monkeypatch, fake)

    result = await clickup_update_folder(UpdateFolderInput(folder_id="456", name="Renamed"))

    assert result.startswith("Error (400)")


# --------------------------------------------------------------------------
# clickup_delete_folder
# --------------------------------------------------------------------------


async def test_delete_folder_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_delete_folder(DeleteFolderInput(folder_id="456"))

    assert result == "Deleted folder `456`."
    assert fake.calls == [("DELETE", "/folder/456", {})]


async def test_delete_folder_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(404))
    _patch_client(monkeypatch, fake)

    result = await clickup_delete_folder(DeleteFolderInput(folder_id="missing"))

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# clickup_create_folder_from_template
# --------------------------------------------------------------------------


async def test_create_folder_from_template_ok_with_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "789"})
    _patch_client(monkeypatch, fake)

    result = await clickup_create_folder_from_template(
        CreateFolderFromTemplateInput(
            space_id="90130912",
            template_id="t-7162342",
            name="Q4 Launch",
            options={"include_views": True, "old_due_date": True},
        )
    )

    assert "Q4 Launch" in result
    assert "789" in result
    assert fake.calls == [
        (
            "POST",
            "/space/90130912/folder_template/t-7162342",
            {"json_body": {"name": "Q4 Launch", "options": {"include_views": True, "old_due_date": True}}},
        )
    ]


async def test_create_folder_from_template_ok_without_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "789"})
    _patch_client(monkeypatch, fake)

    await clickup_create_folder_from_template(
        CreateFolderFromTemplateInput(space_id="1", template_id="t-1", name="Plain")
    )

    assert fake.calls == [("POST", "/space/1/folder_template/t-1", {"json_body": {"name": "Plain"}})]


def test_create_folder_from_template_requires_t_prefix() -> None:
    with pytest.raises(ValidationError):
        CreateFolderFromTemplateInput(space_id="1", template_id="7162342", name="Plain")


async def test_create_folder_from_template_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(404, {"err": "Template not found"}))
    _patch_client(monkeypatch, fake)

    result = await clickup_create_folder_from_template(
        CreateFolderFromTemplateInput(space_id="1", template_id="t-missing", name="X")
    )

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# clickup_get_folder_templates
# --------------------------------------------------------------------------


async def test_get_folder_templates_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"templates": [{"id": "t-1", "name": "Marketing Sprint"}]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder_templates(GetFolderTemplatesInput(team_id="90130912"))

    assert "Folder Templates in Workspace 90130912" in result
    assert "Marketing Sprint" in result
    assert fake.calls == [("GET", "/team/90130912/folder_template", {})]


async def test_get_folder_templates_pagination_slices_client_side(monkeypatch: pytest.MonkeyPatch) -> None:
    templates = [{"id": f"t-{i}", "name": f"Template {i}"} for i in range(5)]
    fake = _FakeClient(payload={"templates": templates})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder_templates(GetFolderTemplatesInput(team_id="1", limit=2, offset=1))

    assert "Template 1" in result
    assert "Template 2" in result
    assert "Template 0" not in result
    assert "Template 3" not in result


async def test_get_folder_templates_bare_array_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive normalization: some responses may hand back a bare array."""
    fake = _FakeClient(payload=[{"id": "t-1", "name": "Bare"}])
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder_templates(GetFolderTemplatesInput(team_id="1"))

    assert "Bare" in result


async def test_get_folder_templates_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_status_error(404))
    _patch_client(monkeypatch, fake)

    result = await clickup_get_folder_templates(GetFolderTemplatesInput(team_id="missing"))

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _status_error(status: int, body: dict[str, Any] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/thing")
    response = httpx.Response(status, json=body or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# --------------------------------------------------------------------------
# live smoke (read tool only; skipped without CLICKUP_API_TOKEN / team id)
# --------------------------------------------------------------------------


@pytest.mark.live
async def test_get_folder_templates_live() -> None:
    team_id = get_settings().clickup_team_id or os.environ.get("CLICKUP_TEAM_ID", "")
    if not team_id:
        pytest.skip("CLICKUP_TEAM_ID not configured for live smoke test")

    result = await clickup_get_folder_templates(GetFolderTemplatesInput(team_id=team_id))

    assert not result.startswith("Error (401)")
