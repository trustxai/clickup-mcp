"""Unit tests for the members/user-groups/custom-roles/shared-hierarchy tools."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.members import (
    CreateUserGroupInput,
    DeleteUserGroupInput,
    GetCustomRolesInput,
    GetListMembersInput,
    GetSharedHierarchyInput,
    GetTaskMembersInput,
    GetUserGroupsInput,
    UpdateUserGroupInput,
    clickup_create_user_group,
    clickup_delete_user_group,
    clickup_get_custom_roles,
    clickup_get_list_members,
    clickup_get_shared_hierarchy,
    clickup_get_task_members,
    clickup_get_user_groups,
    clickup_update_user_group,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload or {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


_MEMBER_A = {
    "id": 812,
    "username": "John Doe",
    "email": "john@example.com",
    "color": "#FFFFFF",
    "initials": "JD",
    "profilePicture": None,
}
_MEMBER_B = {
    "id": 183,
    "username": "Jerry",
    "email": "jerry@example.com",
    "color": "#40BC86",
    "initials": "J",
    "profilePicture": None,
}
_GROUP = {
    "id": "4bfdfcec-6f4f-40a7-b0d6-22660d51870d",
    "team_id": "301540",
    "userid": 301828,
    "name": "Product Managers",
    "handle": "productmanagers",
    "date_created": "1640122639829",
    "initials": "PM",
    "members": [_MEMBER_B],
}
_CUSTOM_ROLE = {
    "id": 4547089,
    "team_id": "301539",
    "name": "guest custom",
    "inherited_role": 4,
    "date_created": "1651189835671",
    "members": [12345, 67899],
}


# --- clickup_get_list_members --------------------------------------------------


async def test_get_list_members_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"members": [_MEMBER_A]})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_list_members(GetListMembersInput(list_id="901300123456"))

    assert "John Doe" in result
    assert "812" in result
    assert fake.calls == [("GET", "/list/901300123456/member", {})]


async def test_get_list_members_empty_is_edge_case(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"members": []})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_list_members(GetListMembersInput(list_id="901300123456"))

    assert "No members" in result
    assert "**0** member(s)" in result


async def test_get_list_members_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"members": [_MEMBER_A]})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_list_members(GetListMembersInput(list_id="901300123456", response_format="json"))

    assert '"count": 1' in result
    assert '"john@example.com"' in result


async def test_get_list_members_truncates_display(monkeypatch: pytest.MonkeyPatch) -> None:
    many_members = [{**_MEMBER_A, "id": i} for i in range(60)]
    fake = _FakeClient(payload={"members": many_members})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_list_members(GetListMembersInput(list_id="901300123456"))

    assert "Showing first 50 of 60" in result


# --- clickup_get_task_members ---------------------------------------------------


async def test_get_task_members_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"members": [_MEMBER_A]})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_task_members(GetTaskMembersInput(task_id="9hz"))

    assert "John Doe" in result
    assert fake.calls == [("GET", "/task/9hz/member", {"params": None})]


async def test_get_task_members_custom_task_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"members": []})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    await clickup_get_task_members(GetTaskMembersInput(task_id="DEV-123", custom_task_ids=True, team_id="123456"))

    method, path, kwargs = fake.calls[0]
    assert method == "GET"
    assert path == "/task/DEV-123/member"
    assert kwargs["params"] == {"custom_task_ids": "true", "team_id": "123456"}


# --- clickup_create_user_group ---------------------------------------------------


async def test_create_user_group_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_GROUP)
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_create_user_group(
        CreateUserGroupInput(team_id="123456", name="Product Managers", members=[183, 812])
    )

    assert "Created User Group" in result
    assert "Product Managers" in result
    assert "4bfdfcec-6f4f-40a7-b0d6-22660d51870d" in result
    method, path, kwargs = fake.calls[0]
    assert method == "POST"
    assert path == "/team/123456/group"
    assert kwargs["json_body"] == {"name": "Product Managers", "members": [183, 812]}


async def test_create_user_group_with_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_GROUP)
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    await clickup_create_user_group(
        CreateUserGroupInput(team_id="123456", name="Product Managers", members=[183], handle="pm")
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["handle"] == "pm"


async def test_create_user_group_requires_members() -> None:
    with pytest.raises(ValidationError):
        CreateUserGroupInput(team_id="123456", name="Empty", members=[])


# --- clickup_get_user_groups ---------------------------------------------------


async def test_get_user_groups_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"groups": [_GROUP]})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_user_groups(GetUserGroupsInput(team_id="123456"))

    assert "Product Managers" in result
    assert "group_id" in result
    method, path, kwargs = fake.calls[0]
    assert method == "GET"
    assert path == "/group"
    assert kwargs["params"] == {"team_id": "123456"}


async def test_get_user_groups_with_group_ids_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"groups": [_GROUP]})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    await clickup_get_user_groups(
        GetUserGroupsInput(team_id="123456", group_ids=["4bfdfcec-6f4f-40a7-b0d6-22660d51870d"])
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["params"]["group_ids"] == ["4bfdfcec-6f4f-40a7-b0d6-22660d51870d"]


# --- clickup_update_user_group ---------------------------------------------------


async def test_update_user_group_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={**_GROUP, "name": "QA Team"})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_update_user_group(
        UpdateUserGroupInput(group_id="4bfdfcec-6f4f-40a7-b0d6-22660d51870d", name="QA Team")
    )

    assert "QA Team" in result
    method, path, kwargs = fake.calls[0]
    assert method == "PUT"
    assert path == "/group/4bfdfcec-6f4f-40a7-b0d6-22660d51870d"
    assert kwargs["json_body"] == {"name": "QA Team"}


async def test_update_user_group_add_remove_members(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_GROUP)
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    await clickup_update_user_group(
        UpdateUserGroupInput(
            group_id="4bfdfcec-6f4f-40a7-b0d6-22660d51870d",
            add_member_ids=[123456],
            remove_member_ids=[159753],
        )
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"] == {"members": {"add": [123456], "rem": [159753]}}


async def test_update_user_group_requires_a_change() -> None:
    with pytest.raises(ValidationError):
        UpdateUserGroupInput(group_id="4bfdfcec-6f4f-40a7-b0d6-22660d51870d")


# --- clickup_delete_user_group ---------------------------------------------------


async def test_delete_user_group_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_delete_user_group(DeleteUserGroupInput(group_id="4bfdfcec-6f4f-40a7-b0d6-22660d51870d"))

    assert "Deleted User Group" in result
    assert fake.calls == [("DELETE", "/group/4bfdfcec-6f4f-40a7-b0d6-22660d51870d", {})]


async def test_delete_user_group_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("DELETE", "https://api.clickup.com/api/v2/group/bad-id")
    response = httpx.Response(404, request=request, json={"err": "Group not found", "ECODE": "GROUP_001"})
    fake = _FakeClient(exc=httpx.HTTPStatusError("not found", request=request, response=response))
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_delete_user_group(DeleteUserGroupInput(group_id="bad-id"))

    assert result.startswith("Error (404)")
    assert "Group not found" in result


# --- clickup_get_custom_roles ---------------------------------------------------


async def test_get_custom_roles_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"custom_roles": [_CUSTOM_ROLE]})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_custom_roles(GetCustomRolesInput(team_id="301539"))

    assert "guest custom" in result
    assert "guest" in result  # inherited_role 4 -> "guest"
    method, path, kwargs = fake.calls[0]
    assert method == "GET"
    assert path == "/team/301539/customroles"
    assert kwargs["params"] == {"include_members": "true"}


async def test_get_custom_roles_exclude_members(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"custom_roles": []})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_custom_roles(GetCustomRolesInput(team_id="301539", include_members=False))

    assert "No Custom Roles" in result
    _, _, kwargs = fake.calls[0]
    assert kwargs["params"] == {"include_members": "false"}


# --- clickup_get_shared_hierarchy ---------------------------------------------------


async def test_get_shared_hierarchy_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "shared": {
            "tasks": ["abc123"],
            "lists": [{"id": "1421", "name": "Shared List"}],
            "folders": [{"id": "1058", "name": "Shared Folder"}],
        }
    }
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_shared_hierarchy(GetSharedHierarchyInput(team_id="123456"))

    assert "Shared List" in result
    assert "Shared Folder" in result
    assert "abc123" in result
    assert fake.calls == [("GET", "/team/123456/shared", {})]


async def test_get_shared_hierarchy_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"shared": {}})
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_shared_hierarchy(GetSharedHierarchyInput(team_id="123456"))

    assert result.count("_None._") == 3


async def test_get_shared_hierarchy_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"shared": {"tasks": [], "lists": [], "folders": []}}
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("clickup_mcp.tools.members.get_client", lambda: fake)

    result = await clickup_get_shared_hierarchy(GetSharedHierarchyInput(team_id="123456", response_format="json"))

    assert '"tasks": []' in result
