"""Unit tests for the Goals & Key Results tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from clickup_mcp.config import get_settings
from clickup_mcp.tools.goals import (
    CreateGoalInput,
    CreateKeyResultInput,
    DeleteGoalInput,
    DeleteKeyResultInput,
    EditKeyResultInput,
    GetGoalInput,
    GetGoalsInput,
    UpdateGoalInput,
    clickup_create_goal,
    clickup_create_key_result,
    clickup_delete_goal,
    clickup_delete_key_result,
    clickup_edit_key_result,
    clickup_get_goal,
    clickup_get_goals,
    clickup_update_goal,
)


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


def _goal(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "goal-1",
        "name": "Grow MRR",
        "percent_completed": 25,
        "due_date": "1735689600000",
        "owners": [{"id": 1, "username": "alej"}],
        "description": "Reach $1M MRR",
        "archived": False,
        "key_results": [
            {
                "id": "kr-1",
                "name": "Close 10 deals",
                "type": "number",
                "steps_current": 4,
                "steps_end": 10,
                "unit": "deals",
                "completed": False,
            }
        ],
    }
    base.update(overrides)
    return base


def _key_result(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "kr-1",
        "name": "Close 10 deals",
        "type": "number",
        "goal_id": "goal-1",
        "steps_current": 4,
        "steps_end": 10,
        "unit": "deals",
        "completed": False,
    }
    base.update(overrides)
    return base


# --- clickup_create_goal ----------------------------------------------------


async def test_create_goal_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"goal": _goal()})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_create_goal(
        CreateGoalInput(team_id="900", name="Grow MRR", due_date=1735689600000, color="#32a852")
    )

    assert "Created goal" in result
    assert "Grow MRR" in result
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("POST", "/team/900/goal")
    assert kwargs["json_body"]["name"] == "Grow MRR"
    assert kwargs["json_body"]["due_date"] == 1735689600000
    assert kwargs["json_body"]["color"] == "#32a852"


async def test_create_goal_missing_team_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_create_goal(CreateGoalInput(name="Grow MRR"))

    assert result.startswith("Error")
    assert "team_id" in result
    assert fake.calls == []


# --- clickup_get_goals -------------------------------------------------------


async def test_get_goals_flattens_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "goals": [_goal(id="goal-1")],
        "folders": [{"id": "folder-1", "goals": [_goal(id="goal-2", name="Ship v2")]}],
    }
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_get_goals(GetGoalsInput(team_id="900"))

    assert "Goals (2)" in result
    assert "Grow MRR" in result
    assert "Ship v2" in result
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("GET", "/team/900/goal")
    assert kwargs["params"] == {"include_completed": "false"}


async def test_get_goals_json_format_includes_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"goals": [_goal()], "folders": []})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_get_goals(GetGoalsInput(team_id="900", include_completed=True, response_format="json"))

    assert '"count": 1' in result
    _, _, kwargs = fake.calls[0]
    assert kwargs["params"] == {"include_completed": "true"}


async def test_get_goals_truncates_display(monkeypatch: pytest.MonkeyPatch) -> None:
    goals = [_goal(id=f"goal-{i}", name=f"Goal {i}") for i in range(60)]
    fake = _FakeClient(payload={"goals": goals, "folders": []})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_get_goals(GetGoalsInput(team_id="900"))

    assert "Goals (60)" in result
    assert "Truncated to 50 of 60" in result


async def test_get_goals_uses_team_id_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_TEAM_ID", "9013")
    get_settings.cache_clear()
    fake = _FakeClient(payload={"goals": [], "folders": []})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_get_goals(GetGoalsInput())

    assert "Goals (0)" in result
    method, path, _ = fake.calls[0]
    assert (method, path) == ("GET", "/team/9013/goal")


# --- clickup_get_goal ---------------------------------------------------------


async def test_get_goal_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"goal": _goal()})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_get_goal(GetGoalInput(goal_id="goal-1"))

    assert "Grow MRR" in result
    assert "Close 10 deals" in result
    assert fake.calls[0][:2] == ("GET", "/goal/goal-1")


async def test_get_goal_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.clickup.com/api/v2/goal/missing")
    response = httpx.Response(404, json={"err": "Goal not found", "ECODE": "GOAL_014"}, request=request)
    exc = httpx.HTTPStatusError("not found", request=request, response=response)
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_get_goal(GetGoalInput(goal_id="missing"))

    assert result.startswith("Error (404)")
    assert "Goal not found" in result


# --- clickup_update_goal -------------------------------------------------------


async def test_update_goal_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"goal": _goal(name="Grow MRR faster")})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_update_goal(UpdateGoalInput(goal_id="goal-1", add_owners=[123]))

    assert "Updated goal" in result
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("PUT", "/goal/goal-1")
    assert kwargs["json_body"] == {"add_owners": [123]}


def test_update_goal_requires_a_field() -> None:
    with pytest.raises(ValueError, match="at least one field"):
        UpdateGoalInput(goal_id="goal-1")


# --- clickup_delete_goal --------------------------------------------------------


async def test_delete_goal_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_delete_goal(DeleteGoalInput(goal_id="goal-1"))

    assert "Deleted goal" in result
    assert fake.calls[0][:2] == ("DELETE", "/goal/goal-1")


# --- clickup_create_key_result ---------------------------------------------------


async def test_create_key_result_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"key_result": _key_result()})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_create_key_result(
        CreateKeyResultInput(
            goal_id="goal-1",
            name="Close 10 deals",
            type="number",
            steps_start=0,
            steps_end=10,
            unit="deals",
            owners=[1],
        )
    )

    assert "Created key result" in result
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("POST", "/goal/goal-1/key_result")
    assert kwargs["json_body"]["type"] == "number"
    assert kwargs["json_body"]["steps_end"] == 10


def test_create_key_result_automatic_requires_source() -> None:
    with pytest.raises(ValueError, match="automatic"):
        CreateKeyResultInput(goal_id="goal-1", name="Auto KR", type="automatic")


# --- clickup_edit_key_result ------------------------------------------------------


async def test_edit_key_result_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"key_result": _key_result(steps_current=5)})
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_edit_key_result(
        EditKeyResultInput(key_result_id="kr-1", steps_current=5, note="Closed another deal.")
    )

    assert "Updated key result" in result
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("PUT", "/key_result/kr-1")
    assert kwargs["json_body"] == {"steps_current": 5, "note": "Closed another deal."}


def test_edit_key_result_requires_a_field() -> None:
    with pytest.raises(ValueError, match="at least one field"):
        EditKeyResultInput(key_result_id="kr-1")


# --- clickup_delete_key_result ------------------------------------------------------


async def test_delete_key_result_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("clickup_mcp.tools.goals.get_client", lambda: fake)

    result = await clickup_delete_key_result(DeleteKeyResultInput(key_result_id="kr-1"))

    assert "Deleted key result" in result
    assert fake.calls[0][:2] == ("DELETE", "/key_result/kr-1")
