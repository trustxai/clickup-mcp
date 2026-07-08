"""Unit tests for the ClickUp Workspace-admin tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.workspace import (
    GetWorkspacePlanInput,
    GetWorkspaceSeatsInput,
    QueryAuditLogsInput,
    UpdatePrivacyAndAccessInput,
    clickup_get_workspace_plan,
    clickup_get_workspace_seats,
    clickup_query_audit_logs,
    clickup_update_privacy_and_access,
)

TEAM = "9008"


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.workspace.get_client", lambda: fake)


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.clickup.com/api/v3/x")
    resp = httpx.Response(status, json={"err": "denied", "ECODE": "OAUTH_027"}, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


# --------------------------------------------------------------------------- plan
async def test_get_workspace_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"plan_name": "Enterprise", "plan_id": 4})
    _patch(monkeypatch, fake)

    result = await clickup_get_workspace_plan(GetWorkspacePlanInput(team_id=TEAM))

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/team/{TEAM}/plan"
    assert "Enterprise" in result and "4" in result


async def test_get_workspace_plan_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"plan_name": "Unlimited", "plan_id": 2})
    _patch(monkeypatch, fake)

    result = await clickup_get_workspace_plan(GetWorkspacePlanInput(team_id=TEAM, response_format="json"))

    assert '"plan_name": "Unlimited"' in result


# --------------------------------------------------------------------------- seats
async def test_get_workspace_seats_infinity_guests(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "members": {"filled_members_seats": 9, "total_member_seats": 9, "empty_members_seats": 0},
            "guests": {"filled_guest_seats": 2, "total_guest_seats": "Infinity", "empty_guest_seats": "Infinity"},
        }
    )
    _patch(monkeypatch, fake)

    result = await clickup_get_workspace_seats(GetWorkspaceSeatsInput(team_id=TEAM))

    call = fake.calls[0]
    assert call["path"] == f"/team/{TEAM}/seats"
    assert "Members" in result and "Guests" in result
    assert "Infinity" in result  # unlimited guest seats rendered as-is


# --------------------------------------------------------------------------- audit logs
async def test_query_audit_logs_body_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={"data": [{"eventType": "CHANGE_PASSWORD", "eventStatus": "failed", "userEmail": "a@x.io"}]}
    )
    _patch(monkeypatch, fake)

    result = await clickup_query_audit_logs(
        QueryAuditLogsInput(
            workspace_id=TEAM,
            applicability="auth-and-security",
            user_emails=["a@x.io"],
            event_status="failed",
            start_time=1718754539000,
            page_rows=50,
        )
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{TEAM}/auditlogs"
    assert call["use_v3"] is True
    body = call["json_body"]
    assert body["applicability"] == "auth-and-security"
    assert body["filter"]["workspaceId"] == TEAM
    assert body["filter"]["userEmail"] == ["a@x.io"]
    assert body["filter"]["eventStatus"] == "failed"
    assert body["filter"]["startTime"] == 1718754539000
    assert body["pagination"]["pageRows"] == 50
    assert body["pagination"]["pageDirection"] == "before"
    assert "CHANGE_PASSWORD" in result


async def test_query_audit_logs_pagination_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [{"eventType": "USER_LOGIN", "timestamp": 1727221739000}]})
    _patch(monkeypatch, fake)

    result = await clickup_query_audit_logs(
        QueryAuditLogsInput(
            workspace_id=TEAM,
            applicability="user-activity",
            page_timestamp=1727000000000,
            page_direction="after",
        )
    )

    pagination = fake.calls[0]["json_body"]["pagination"]
    assert pagination["pageTimestamp"] == 1727000000000
    assert pagination["pageDirection"] == "after"
    # the last row timestamp is surfaced as the next-page hint
    assert "1727221739000" in result


async def test_query_audit_logs_json_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": []})
    _patch(monkeypatch, fake)

    result = await clickup_query_audit_logs(
        QueryAuditLogsInput(workspace_id=TEAM, applicability="other-activity", response_format="json")
    )

    assert '"count": 0' in result


async def test_query_audit_logs_enterprise_403(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403))
    _patch(monkeypatch, fake)

    result = await clickup_query_audit_logs(
        QueryAuditLogsInput(workspace_id=TEAM, applicability="auth-and-security")
    )

    assert result.startswith("Error (403)")
    assert "Enterprise" in result


# --------------------------------------------------------------------------- ACL patch
async def test_update_privacy_and_access_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_update_privacy_and_access(
        UpdatePrivacyAndAccessInput(
            workspace_id=TEAM,
            object_type="list",
            object_id="901300",
            private=True,
            entries=[{"kind": "group", "id": "88", "permission_level": 4}],
        )
    )

    call = fake.calls[0]
    assert call["method"] == "PATCH"
    assert call["path"] == f"/workspaces/{TEAM}/list/901300/acls"
    assert call["use_v3"] is True
    body = call["json_body"]
    assert body["private"] is True
    assert body["entries"] == [{"kind": "group", "id": "88", "permission_level": 4}]
    assert "private" in result and "edit" in result


async def test_update_privacy_and_access_remove_access(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_update_privacy_and_access(
        UpdatePrivacyAndAccessInput(
            workspace_id=TEAM,
            object_type="doc",
            object_id="8cb",
            entries=[{"kind": "user", "id": "182", "permission_level": None}],
        )
    )

    body = fake.calls[0]["json_body"]
    # null permission_level must be sent verbatim (means "remove access")
    assert body["entries"][0]["permission_level"] is None
    assert "private" not in body
    assert "remove access" in result


async def test_update_privacy_and_access_requires_a_change() -> None:
    with pytest.raises(ValidationError):
        UpdatePrivacyAndAccessInput(object_type="list", object_id="901300")


# --------------------------------------------------------------------------- live smoke (read-only)
@pytest.mark.live
async def test_get_workspace_plan_live() -> None:
    result = await clickup_get_workspace_plan(GetWorkspacePlanInput())
    assert isinstance(result, str)
    assert not result.startswith("Error")


@pytest.mark.live
async def test_get_workspace_seats_live() -> None:
    result = await clickup_get_workspace_seats(GetWorkspaceSeatsInput())
    assert isinstance(result, str)
    assert not result.startswith("Error")
