"""Unit tests for the Spaces tool module against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.spaces import (
    CreateSpaceInput,
    DeleteSpaceInput,
    GetSpaceInput,
    GetSpacesInput,
    UpdateSpaceInput,
    clickup_create_space,
    clickup_delete_space,
    clickup_get_space,
    clickup_get_spaces,
    clickup_update_space,
)

SPACE_PAYLOAD: dict[str, Any] = {
    "id": "90130012345",
    "name": "Engineering",
    "private": False,
    "multiple_assignees": True,
    "statuses": [
        {"status": "to do", "type": "open", "orderindex": 0, "color": "#d3d3d3"},
        {"status": "in progress", "type": "custom", "orderindex": 1, "color": "#a875ff"},
        {"status": "done", "type": "closed", "orderindex": 2, "color": "#6bc950"},
    ],
    "features": {
        "due_dates": {
            "enabled": True,
            "start_date": False,
            "remap_due_dates": True,
            "remap_closed_due_date": False,
        },
        "time_tracking": {"enabled": False},
        "tags": {"enabled": True},
        "time_estimates": {"enabled": True},
        "checklists": {"enabled": True},
        "custom_fields": {"enabled": True},
        "remap_dependencies": {"enabled": True},
        "dependency_warning": {"enabled": True},
        "portfolios": {"enabled": False},
    },
}


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


class _FakeSettings:
    def __init__(self, team_id: str = "") -> None:
        self.clickup_team_id = team_id


# --- create_space -----------------------------------------------------------


async def test_create_space_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    params = CreateSpaceInput(
        team_id="90100000",
        name="Engineering",
        multiple_assignees=True,
        features={"due_dates": {"enabled": True}, "time_tracking": {"enabled": False}},
        statuses=[
            {"status": "to do", "type": "open", "orderindex": 0, "color": "#d3d3d3"},
            {"status": "done", "type": "closed", "orderindex": 1, "color": "#6bc950"},
        ],
    )
    result = await clickup_create_space(params)

    assert "Created Space" in result
    assert "Engineering" in result
    assert "90130012345" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/team/90100000/space"
    body = kwargs["json_body"]
    assert body["name"] == "Engineering"
    assert body["multiple_assignees"] is True
    assert body["features"]["due_dates"]["enabled"] is True
    assert body["features"]["time_tracking"]["enabled"] is False
    assert "tags" not in body["features"]
    assert body["statuses"][0]["status"] == "to do"


async def test_create_space_uses_default_team_id_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_settings", lambda: _FakeSettings(team_id="90199999"))

    params = CreateSpaceInput(name="Engineering")
    result = await clickup_create_space(params)

    assert "Created Space" in result
    _, path, _ = fake.calls[0]
    assert path == "/team/90199999/space"


async def test_create_space_missing_team_id_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_settings", lambda: _FakeSettings(team_id=""))

    params = CreateSpaceInput(name="Engineering")
    result = await clickup_create_space(params)

    assert result.startswith("Error")
    assert "team_id" in result
    assert fake.calls == []


# --- get_spaces (list + pagination) -----------------------------------------


async def test_get_spaces_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "spaces": [
            {"id": "1", "name": "Alpha", "private": False, "multiple_assignees": True},
            {"id": "2", "name": "Beta", "private": True, "multiple_assignees": False},
        ]
    }
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_get_spaces(GetSpacesInput(team_id="90100000"))

    assert "Alpha" in result
    assert "Beta" in result
    assert "Showing **2** of total **2**" in result
    method, path, kwargs = fake.calls[0]
    assert method == "GET"
    assert path == "/team/90100000/space"
    assert kwargs["params"] == {"archived": "false"}


async def test_get_spaces_archived_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"spaces": []})
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    await clickup_get_spaces(GetSpacesInput(team_id="90100000", archived=True))

    _, _, kwargs = fake.calls[0]
    assert kwargs["params"] == {"archived": "true"}


async def test_get_spaces_pagination_windowing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "spaces": [{"id": str(i), "name": f"Space {i}", "private": False, "multiple_assignees": True} for i in range(5)]
    }
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    first_page = await clickup_get_spaces(GetSpacesInput(team_id="90100000", limit=2, offset=0))
    assert "Space 0" in first_page
    assert "Space 1" in first_page
    assert "Space 2" not in first_page
    assert "next offset → **2**" in first_page

    last_page = await clickup_get_spaces(GetSpacesInput(team_id="90100000", limit=2, offset=4))
    assert "Space 4" in last_page
    assert "More available" not in last_page


async def test_get_spaces_display_cap_clamps_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "spaces": [
            {"id": str(i), "name": f"Space {i}", "private": False, "multiple_assignees": True} for i in range(60)
        ]
    }
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_get_spaces(GetSpacesInput(team_id="90100000", limit=100, offset=0))

    assert "Showing **50** of total **60**" in result


async def test_get_spaces_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"spaces": [{"id": "1", "name": "Alpha", "private": False, "multiple_assignees": True}]}
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_get_spaces(GetSpacesInput(team_id="90100000", response_format=ResponseFormat.JSON))

    assert '"title"' in result
    assert '"Alpha"' in result


# --- get_space ----------------------------------------------------------------


async def test_get_space_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_get_space(GetSpaceInput(space_id="90130012345"))

    assert "Engineering" in result
    assert "to do" in result
    assert "in progress" in result
    assert "**Enabled**" in result
    assert "due_dates" in result
    assert "**Disabled**" in result
    assert "time_tracking" in result
    method, path, _ = fake.calls[0]
    assert method == "GET"
    assert path == "/space/90130012345"


async def test_get_space_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_get_space(GetSpaceInput(space_id="90130012345", response_format=ResponseFormat.JSON))

    assert '"name": "Engineering"' in result


async def test_get_space_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/space/999")
    response = httpx.Response(404, json={"err": "Space not found", "ECODE": "SPACE_004"}, request=request)
    exc = httpx.HTTPStatusError("Not Found", request=request, response=response)
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_get_space(GetSpaceInput(space_id="999"))

    assert result.startswith("Error (404)")
    assert "space_id" in result


# --- update_space --------------------------------------------------------------


async def test_update_space_partial_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={**SPACE_PAYLOAD, "name": "Renamed"})
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    params = UpdateSpaceInput(space_id="90130012345", name="Renamed", private=True)
    result = await clickup_update_space(params)

    assert "Updated Space" in result
    assert "Renamed" in result
    assert "name" in result
    assert "private" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert path == "/space/90130012345"
    body = kwargs["json_body"]
    assert body == {"name": "Renamed", "private": True}


async def test_update_space_statuses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    params = UpdateSpaceInput(
        space_id="90130012345",
        statuses=[
            {"status": "to do", "type": "open", "orderindex": 0, "color": "#d3d3d3"},
            {"status": "blocked", "type": "custom", "orderindex": 1, "color": "#e50000"},
            {"status": "done", "type": "closed", "orderindex": 2, "color": "#6bc950"},
        ],
    )
    await clickup_update_space(params)

    _, _, kwargs = fake.calls[0]
    body = kwargs["json_body"]
    assert [s["status"] for s in body["statuses"]] == ["to do", "blocked", "done"]


async def test_update_space_features_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SPACE_PAYLOAD)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    params = UpdateSpaceInput(space_id="90130012345", features={"time_tracking": {"enabled": True}})
    await clickup_update_space(params)

    _, _, kwargs = fake.calls[0]
    body = kwargs["json_body"]
    assert body["features"] == {"time_tracking": {"enabled": True}}


# --- delete_space ---------------------------------------------------------------


async def test_delete_space_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_delete_space(DeleteSpaceInput(space_id="90130012345"))

    assert "Deleted Space" in result
    assert "90130012345" in result
    method, path, _ = fake.calls[0]
    assert method == "DELETE"
    assert path == "/space/90130012345"


async def test_delete_space_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("DELETE", "https://api.clickup.com/api/v2/space/1")
    response = httpx.Response(403, json={"err": "Forbidden", "ECODE": "OAUTH_027"}, request=request)
    exc = httpx.HTTPStatusError("Forbidden", request=request, response=response)
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("clickup_mcp.tools.spaces.get_client", lambda: fake)

    result = await clickup_delete_space(DeleteSpaceInput(space_id="1"))

    assert result.startswith("Error (403)")


# --- input validation (extra="forbid") -------------------------------------


def test_create_space_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CreateSpaceInput.model_validate({"name": "Engineering", "bogus_field": "nope"})
