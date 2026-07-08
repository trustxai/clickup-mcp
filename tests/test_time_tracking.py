"""Unit tests for the time-tracking tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.time_tracking import (
    AddTimeEntryTagsInput,
    CreateTimeEntryInput,
    DeleteTimeEntryInput,
    GetRunningTimeEntryInput,
    GetTimeEntriesInput,
    GetTimeEntryHistoryInput,
    GetTimeEntryInput,
    GetTimeEntryTagsInput,
    RemoveTimeEntryTagsInput,
    RenameTimeEntryTagInput,
    ReplaceTimeEstimatesByUserInput,
    StartTimeEntryInput,
    StopTimeEntryInput,
    TimeEntryTagInput,
    TimeEstimateInput,
    UpdateTimeEntryInput,
    UpdateTimeEstimatesByUserInput,
    clickup_add_time_entry_tags,
    clickup_create_time_entry,
    clickup_delete_time_entry,
    clickup_get_running_time_entry,
    clickup_get_time_entries,
    clickup_get_time_entry,
    clickup_get_time_entry_history,
    clickup_get_time_entry_tags,
    clickup_remove_time_entry_tags,
    clickup_rename_time_entry_tag,
    clickup_replace_time_estimates_by_user,
    clickup_start_time_entry,
    clickup_stop_time_entry,
    clickup_update_time_entry,
    clickup_update_time_estimates_by_user,
)


class _FakeResponse:
    def __init__(self, payload: Any = None, raise_on_json: bool = False) -> None:
        self._payload = payload
        self._raise_on_json = raise_on_json

    def json(self) -> Any:
        if self._raise_on_json:
            raise ValueError("no body")
        return self._payload


class _FakeClient:
    def __init__(
        self,
        payload: Any = None,
        exc: Exception | None = None,
        raise_on_json: bool = False,
    ) -> None:
        self._payload = payload
        self._exc = exc
        self._raise_on_json = raise_on_json
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload, self._raise_on_json)


class _FakeSettings:
    def __init__(self, clickup_team_id: str = "") -> None:
        self.clickup_team_id = clickup_team_id


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.time_tracking.get_client", lambda: fake)


SAMPLE_ENTRY = {
    "id": "1963465985517105840",
    "task": {"id": "abc123", "name": "Write docs"},
    "user": {"id": 1, "username": "alejandro"},
    "start": 1700000000000,
    "end": None,
    "duration": -25655,
    "billable": True,
    "tags": [{"name": "writing"}],
}


# --------------------------------------------------------------------------
# clickup_get_time_entries
# --------------------------------------------------------------------------


async def test_get_time_entries_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [SAMPLE_ENTRY]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entries(GetTimeEntriesInput(team_id="123", is_billable=True))

    assert "Time Entries" in result
    assert "RUNNING" in result  # negative duration explained
    assert fake.calls[0][0] == "GET"
    assert fake.calls[0][1] == "/team/123/time_entries"
    assert fake.calls[0][2]["params"]["is_billable"] is True


async def test_get_time_entries_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [SAMPLE_ENTRY]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entries(GetTimeEntriesInput(team_id="123", response_format=ResponseFormat.JSON))

    assert '"count": 1' in result


async def test_get_time_entries_truncates_display(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [{**SAMPLE_ENTRY, "id": str(i)} for i in range(60)]
    fake = _FakeClient(payload={"data": entries})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entries(GetTimeEntriesInput(team_id="123"))

    assert "Found **60** entries" in result
    assert "Showing first 50 of 60" in result


async def test_get_time_entries_missing_team_id_errors() -> None:
    result = await clickup_get_time_entries(GetTimeEntriesInput())

    assert result.startswith("Error")
    assert "team_id is required" in result


async def test_get_time_entries_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("refused"))
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entries(GetTimeEntriesInput(team_id="123"))

    assert result.startswith("Error")
    assert "could not connect" in result


async def test_get_time_entries_default_team_id_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": []})
    _patch_client(monkeypatch, fake)
    monkeypatch.setattr("clickup_mcp.tools.time_tracking.get_settings", lambda: _FakeSettings(clickup_team_id="999"))

    await clickup_get_time_entries(GetTimeEntriesInput())

    assert fake.calls[0][1] == "/team/999/time_entries"


# --------------------------------------------------------------------------
# clickup_get_time_entry
# --------------------------------------------------------------------------


async def test_get_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [SAMPLE_ENTRY]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entry(GetTimeEntryInput(team_id="123", time_entry_id="abc"))

    assert "abc" in result
    assert "RUNNING" in result
    assert fake.calls[0][1] == "/team/123/time_entries/abc"


async def test_get_time_entry_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": []})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entry(GetTimeEntryInput(team_id="123", time_entry_id="missing"))

    assert "No time entry found" in result


# --------------------------------------------------------------------------
# clickup_get_time_entry_history
# --------------------------------------------------------------------------


async def test_get_time_entry_history_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [{"field": "duration", "before": 1000, "after": 2000, "user": 1}]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entry_history(GetTimeEntryHistoryInput(team_id="123", time_entry_id="abc"))

    assert "field=duration" in result
    assert fake.calls[0][1] == "/team/123/time_entries/abc/history"


# --------------------------------------------------------------------------
# clickup_get_running_time_entry
# --------------------------------------------------------------------------


async def test_get_running_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": SAMPLE_ENTRY})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_running_time_entry(GetRunningTimeEntryInput(team_id="123"))

    assert "Running Time Entry" in result
    assert "RUNNING" in result


async def test_get_running_time_entry_none_running(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": None})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_running_time_entry(GetRunningTimeEntryInput(team_id="123"))

    assert "No timer is currently running" in result


# --------------------------------------------------------------------------
# clickup_create_time_entry
# --------------------------------------------------------------------------


async def test_create_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": SAMPLE_ENTRY})
    _patch_client(monkeypatch, fake)

    result = await clickup_create_time_entry(
        CreateTimeEntryInput(
            team_id="123",
            start=1700000000000,
            duration=3600000,
            tags=[TimeEntryTagInput(name="writing")],
        )
    )

    assert "Created time entry" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/team/123/time_entries"
    assert kwargs["json_body"]["tags"] == [{"name": "writing"}]


def test_create_time_entry_requires_duration_or_stop() -> None:
    with pytest.raises(ValidationError):
        CreateTimeEntryInput(team_id="123", start=1700000000000)


# --------------------------------------------------------------------------
# clickup_update_time_entry
# --------------------------------------------------------------------------


async def test_update_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": SAMPLE_ENTRY})
    _patch_client(monkeypatch, fake)

    result = await clickup_update_time_entry(
        UpdateTimeEntryInput(
            team_id="123",
            time_entry_id="abc",
            tags=[TimeEntryTagInput(name="billing")],
            tag_action="add",
        )
    )

    assert "Updated time entry abc" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert kwargs["json_body"]["tag_action"] == "add"


def test_update_time_entry_requires_start_end_pair() -> None:
    with pytest.raises(ValidationError):
        UpdateTimeEntryInput(team_id="123", time_entry_id="abc", start=1700000000000)


# --------------------------------------------------------------------------
# clickup_delete_time_entry
# --------------------------------------------------------------------------


async def test_delete_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_delete_time_entry(DeleteTimeEntryInput(team_id="123", time_entry_id="111,222"))

    assert "Deleted time entry id(s): 111,222" in result
    assert fake.calls[0] == ("DELETE", "/team/123/time_entries/111,222", {})


# --------------------------------------------------------------------------
# clickup_start_time_entry / clickup_stop_time_entry
# --------------------------------------------------------------------------


async def test_start_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": SAMPLE_ENTRY})
    _patch_client(monkeypatch, fake)

    result = await clickup_start_time_entry(StartTimeEntryInput(team_id="123", tid="abc123"))

    assert "Started timer" in result
    assert fake.calls[0][1] == "/team/123/time_entries/start"


async def test_stop_time_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": {**SAMPLE_ENTRY, "duration": 3600000}})
    _patch_client(monkeypatch, fake)

    result = await clickup_stop_time_entry(StopTimeEntryInput(team_id="123"))

    assert "Stopped timer" in result
    assert fake.calls[0][1] == "/team/123/time_entries/stop"


async def test_stop_time_entry_none_running(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_stop_time_entry(StopTimeEntryInput(team_id="123"))

    assert result == "Stopped timer."


# --------------------------------------------------------------------------
# clickup_get_time_entry_tags / add / remove / rename
# --------------------------------------------------------------------------


async def test_get_time_entry_tags_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [{"name": "billing", "tag_fg": "#fff", "tag_bg": "#000", "creator": 1}]})
    _patch_client(monkeypatch, fake)

    result = await clickup_get_time_entry_tags(GetTimeEntryTagsInput(team_id="123"))

    assert "billing" in result
    assert fake.calls[0][1] == "/team/123/time_entries/tags"


async def test_add_time_entry_tags_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_add_time_entry_tags(
        AddTimeEntryTagsInput(
            team_id="123",
            time_entry_ids=["abc", "def"],
            tags=[TimeEntryTagInput(name="billing", tag_bg="#BF55EC")],
        )
    )

    assert "Added tag(s) [billing] to 2 time entries" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert kwargs["json_body"]["time_entry_ids"] == ["abc", "def"]


async def test_remove_time_entry_tags_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_remove_time_entry_tags(
        RemoveTimeEntryTagsInput(team_id="123", time_entry_ids=["abc"], tags=[TimeEntryTagInput(name="billing")])
    )

    assert "Removed tag(s) [billing] from 1 time entry" in result
    assert fake.calls[0][0] == "DELETE"


async def test_rename_time_entry_tag_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch_client(monkeypatch, fake)

    result = await clickup_rename_time_entry_tag(
        RenameTimeEntryTagInput(team_id="123", name="billing", new_name="client-billing", tag_bg="#000", tag_fg="#fff")
    )

    assert "Renamed time entry tag 'billing' -> 'client-billing'" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert kwargs["json_body"]["new_name"] == "client-billing"


async def test_add_time_entry_tags_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        exc=httpx.HTTPStatusError(
            "bad", request=httpx.Request("POST", "https://x"), response=httpx.Response(404, json={"err": "not found"})
        )
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_add_time_entry_tags(
        AddTimeEntryTagsInput(team_id="123", time_entry_ids=["abc"], tags=[TimeEntryTagInput(name="x")])
    )

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# clickup_replace_time_estimates_by_user / clickup_update_time_estimates_by_user
# --------------------------------------------------------------------------


async def test_replace_time_estimates_by_user_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "data": {"total_time_estimate": 10800000, "assignee_estimates": {"300001": 3600000, "300002": 7200000}}
        }
    )
    _patch_client(monkeypatch, fake)

    result = await clickup_replace_time_estimates_by_user(
        ReplaceTimeEstimatesByUserInput(
            team_id="123",
            task_id="abc123",
            estimates=[
                TimeEstimateInput(assignee=300001, time=3600000),
                TimeEstimateInput(assignee="unassigned", time=0),
            ],
        )
    )

    assert "Replaced time estimates for task abc123" in result
    assert "user 300001" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert path == "/workspaces/123/tasks/abc123/time_estimates_by_user"
    assert kwargs["use_v3"] is True
    assert kwargs["json_body"] == [{"assignee": 300001, "time": 3600000}, {"assignee": "unassigned", "time": 0}]


async def test_update_time_estimates_by_user_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": {"total_time_estimate": 14400000, "updated_estimates": {"300001": 5400000}}})
    _patch_client(monkeypatch, fake)

    result = await clickup_update_time_estimates_by_user(
        UpdateTimeEstimatesByUserInput(
            team_id="123", task_id="abc123", estimates=[TimeEstimateInput(assignee=300001, time=5400000)]
        )
    )

    assert "Updated time estimates for task abc123" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PATCH"
    assert kwargs["use_v3"] is True


def test_time_estimates_input_max_ten_items() -> None:
    with pytest.raises(ValidationError):
        ReplaceTimeEstimatesByUserInput(
            team_id="123",
            task_id="abc123",
            estimates=[TimeEstimateInput(assignee=i, time=1000) for i in range(11)],
        )
