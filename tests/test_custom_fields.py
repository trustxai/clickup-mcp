"""Unit tests for the Custom Fields tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.custom_fields import (
    GetCustomTaskTypesInput,
    GetFolderCustomFieldsInput,
    GetListCustomFieldsInput,
    GetSpaceCustomFieldsInput,
    GetTeamCustomFieldsInput,
    RemoveCustomFieldValueInput,
    SetCustomFieldValueInput,
    clickup_get_custom_task_types,
    clickup_get_folder_custom_fields,
    clickup_get_list_custom_fields,
    clickup_get_space_custom_fields,
    clickup_get_team_custom_fields,
    clickup_remove_custom_field_value,
    clickup_set_custom_field_value,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _Call:
    def __init__(self, method: str, path: str, params: Any, json_body: Any) -> None:
        self.method = method
        self.path = path
        self.params = params
        self.json_body = json_body


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload or {}
        self._exc = exc
        self.calls: list[_Call] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_body: Any = None,
        **_: Any,
    ) -> _FakeResponse:
        self.calls.append(_Call(method, path, params, json_body))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.custom_fields.get_client", lambda: fake)


_FIELDS_PAYLOAD = {
    "fields": [
        {
            "id": "abc-123",
            "name": "Priority",
            "type": "drop_down",
            "required": True,
            "type_config": {
                "options": [
                    {"id": "opt-hi", "name": "High", "orderindex": 0},
                    {"id": "opt-lo", "name": "Low", "orderindex": 1},
                ]
            },
        }
    ]
}


# --------------------------------------------------------------------------- #
# Field catalogue reads
# --------------------------------------------------------------------------- #
async def test_get_list_custom_fields_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_FIELDS_PAYLOAD)
    _patch(monkeypatch, fake)

    result = await clickup_get_list_custom_fields(GetListCustomFieldsInput(list_id="900"))

    assert "List Custom Fields" in result
    assert "Priority" in result
    assert "drop_down" in result
    assert "abc-123" in result
    # option UUIDs must be exposed so an LLM can set a drop_down value
    assert "opt-hi" in result
    call = fake.calls[0]
    assert (call.method, call.path) == ("GET", "/list/900/field")
    # include_applied_objects defaults false -> no query params sent
    assert call.params is None


async def test_get_list_custom_fields_json_and_include_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_FIELDS_PAYLOAD)
    _patch(monkeypatch, fake)

    result = await clickup_get_list_custom_fields(
        GetListCustomFieldsInput(list_id="900", include_applied_objects=True, response_format="json")
    )

    parsed = json.loads(result)
    assert parsed["count"] == 1
    assert parsed["fields"][0]["id"] == "abc-123"
    assert fake.calls[0].params == {"include_applied_objects": "true"}


async def test_get_folder_custom_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_FIELDS_PAYLOAD)
    _patch(monkeypatch, fake)

    result = await clickup_get_folder_custom_fields(GetFolderCustomFieldsInput(folder_id="457"))

    assert "Folder Custom Fields" in result
    assert fake.calls[0].path == "/folder/457/field"


async def test_get_space_custom_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_FIELDS_PAYLOAD)
    _patch(monkeypatch, fake)

    result = await clickup_get_space_custom_fields(GetSpaceCustomFieldsInput(space_id="790"))

    assert "Space Custom Fields" in result
    assert fake.calls[0].path == "/space/790/field"


async def test_get_team_custom_fields_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"fields": []})
    _patch(monkeypatch, fake)

    result = await clickup_get_team_custom_fields(GetTeamCustomFieldsInput(team_id="9007200144"))

    assert "Workspace Custom Fields" in result
    assert "No Custom Fields" in result
    assert fake.calls[0].path == "/team/9007200144/field"


# --------------------------------------------------------------------------- #
# Set value — polymorphic body shapes
# --------------------------------------------------------------------------- #
async def test_set_value_text_scalar(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_set_custom_field_value(
        SetCustomFieldValueInput(task_id="9hz", field_id="f-uuid", value="In review")
    )

    assert "Set Custom Field" in result and "9hz" in result
    call = fake.calls[0]
    assert (call.method, call.path) == ("POST", "/task/9hz/field/f-uuid")
    assert call.json_body == {"value": "In review"}
    assert call.params is None  # no custom_task_ids -> no query


async def test_set_value_date_with_value_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    await clickup_set_custom_field_value(
        SetCustomFieldValueInput(task_id="9hz", field_id="f-uuid", value=1667367645000, value_options={"time": True})
    )

    assert fake.calls[0].json_body == {"value": 1667367645000, "value_options": {"time": True}}


async def test_set_value_labels_array(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    await clickup_set_custom_field_value(
        SetCustomFieldValueInput(task_id="9hz", field_id="f-uuid", value=["uuidA", "uuidB"])
    )

    # labels take a plain array of option UUIDs (not an add/rem object)
    assert fake.calls[0].json_body == {"value": ["uuidA", "uuidB"]}


async def test_set_value_task_relationship_add_rem(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    await clickup_set_custom_field_value(
        SetCustomFieldValueInput(task_id="9hz", field_id="f-uuid", value={"add": ["abcd1234"], "rem": ["jklm9876"]})
    )

    assert fake.calls[0].json_body == {"value": {"add": ["abcd1234"], "rem": ["jklm9876"]}}


async def test_set_value_location(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    value = {"location": {"lat": -28.016, "lng": 153.4}, "formatted_address": "Gold Coast QLD, Australia"}
    await clickup_set_custom_field_value(SetCustomFieldValueInput(task_id="9hz", field_id="f-uuid", value=value))

    assert fake.calls[0].json_body == {"value": value}


async def test_set_value_custom_task_ids_query(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    await clickup_set_custom_field_value(
        SetCustomFieldValueInput(
            task_id="ABC-123", field_id="f-uuid", value=42, custom_task_ids=True, team_id="9007200144"
        )
    )

    call = fake.calls[0]
    assert call.params == {"custom_task_ids": "true", "team_id": "9007200144"}
    assert call.json_body == {"value": 42}


# --------------------------------------------------------------------------- #
# Remove value
# --------------------------------------------------------------------------- #
async def test_remove_value(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_remove_custom_field_value(RemoveCustomFieldValueInput(task_id="9hz", field_id="f-uuid"))

    assert "Cleared Custom Field" in result
    call = fake.calls[0]
    assert (call.method, call.path) == ("DELETE", "/task/9hz/field/f-uuid")
    assert call.params is None


async def test_remove_value_custom_task_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    await clickup_remove_custom_field_value(
        RemoveCustomFieldValueInput(task_id="ABC-1", field_id="f", custom_task_ids=True, team_id="42")
    )

    assert fake.calls[0].params == {"custom_task_ids": "true", "team_id": "42"}


# --------------------------------------------------------------------------- #
# Custom task types
# --------------------------------------------------------------------------- #
async def test_get_custom_task_types_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "custom_items": [
                {"id": 1300, "name": "Bug", "name_plural": "Bugs", "description": "A defect"},
            ]
        }
    )
    _patch(monkeypatch, fake)

    result = await clickup_get_custom_task_types(GetCustomTaskTypesInput(team_id="9007200144"))

    assert "Custom Task Types" in result
    assert "Bug" in result and "1300" in result
    assert fake.calls[0].path == "/team/9007200144/custom_item"


async def test_get_custom_task_types_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"custom_items": [{"id": 1300, "name": "Bug"}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_custom_task_types(GetCustomTaskTypesInput(team_id="9007200144", response_format="json"))

    parsed = json.loads(result)
    assert parsed["count"] == 1
    assert parsed["custom_items"][0]["name"] == "Bug"


async def test_get_custom_task_types_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"custom_items": []})
    _patch(monkeypatch, fake)

    result = await clickup_get_custom_task_types(GetCustomTaskTypesInput(team_id="9007200144"))

    assert "No custom task types" in result


# --------------------------------------------------------------------------- #
# Error path + input validation
# --------------------------------------------------------------------------- #
async def test_error_path_maps_to_string(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("refused"))
    _patch(monkeypatch, fake)

    result = await clickup_get_list_custom_fields(GetListCustomFieldsInput(list_id="900"))

    assert result.startswith("Error")
    assert "could not connect" in result


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        SetCustomFieldValueInput(task_id="9hz", field_id="f", value=1, bogus="x")  # type: ignore[call-arg]
