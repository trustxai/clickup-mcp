"""Unit tests for the ClickUp Chat channel v3 tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.chat_channels import (
    MAX_OUTPUT_CHARS,
    ChannelMembersInput,
    CreateChannelInput,
    CreateDirectMessageInput,
    CreateLocationChannelInput,
    DeleteChannelInput,
    GetChannelInput,
    GetChannelsInput,
    UpdateChannelInput,
    clickup_create_chat_channel,
    clickup_create_direct_message,
    clickup_create_location_chat_channel,
    clickup_delete_chat_channel,
    clickup_get_chat_channel,
    clickup_get_chat_channel_followers,
    clickup_get_chat_channel_members,
    clickup_get_chat_channels,
    clickup_update_chat_channel,
)

WS = "9001"


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
    monkeypatch.setattr("clickup_mcp.tools.chat_channels.get_client", lambda: fake)


# --------------------------------------------------------------------------- get channels
async def test_get_channels_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [{"id": "c1", "name": "General", "type": "CHANNEL", "visibility": "PUBLIC"}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channels(
        GetChannelsInput(workspace_id=WS, is_follower=True, channel_types=["CHANNEL", "DM"])
    )

    assert "General" in result and "c1" in result
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/workspaces/{WS}/chat/channels"
    assert call["use_v3"] is True
    assert call["params"]["description_format"] == "text/md"
    assert call["params"]["is_follower"] is True
    assert call["params"]["include_closed"] is False
    assert call["params"]["limit"] == 50
    # list enum values are serialized to plain strings
    assert call["params"]["channel_types"] == ["CHANNEL", "DM"]


async def test_get_channels_cursor_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"channels": [{"id": "c1", "name": "A"}], "next_cursor": "CUR2"})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channels(GetChannelsInput(workspace_id=WS, cursor="CUR1"))

    # inbound cursor forwarded, outbound next_cursor surfaced
    assert fake.calls[0]["params"]["cursor"] == "CUR1"
    assert "CUR2" in result


async def test_get_channels_json_format_bare_array(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[{"id": "c1", "name": "Bare"}])  # bare-array response shape
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channels(GetChannelsInput(workspace_id=WS, response_format="json"))

    assert '"count": 1' in result
    assert "Bare" in result


async def test_get_channels_json_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = {"id": "c1", "name": "x" * (MAX_OUTPUT_CHARS + 1_000)}
    fake = _FakeClient(payload={"data": [huge]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channels(GetChannelsInput(workspace_id=WS, response_format="json"))

    assert result.endswith("…output truncated (response exceeded display cap).")


async def test_get_channels_with_message_since(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": []})
    _patch(monkeypatch, fake)

    await clickup_get_chat_channels(GetChannelsInput(workspace_id=WS, with_message_since=1700000000000))

    assert fake.calls[0]["params"]["with_message_since"] == 1700000000000


# --------------------------------------------------------------------------- create channel
async def test_create_channel_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "c9", "name": "announcements", "visibility": "PUBLIC"})
    _patch(monkeypatch, fake)

    result = await clickup_create_chat_channel(
        CreateChannelInput(workspace_id=WS, name="announcements", visibility="PUBLIC", user_ids=["1", "2"])
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{WS}/chat/channels"
    assert call["json_body"]["name"] == "announcements"
    assert call["json_body"]["visibility"] == "PUBLIC"
    assert call["json_body"]["user_ids"] == ["1", "2"]
    assert "announcements" in result and "c9" in result


async def test_create_channel_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "c0", "name": "team"})
    _patch(monkeypatch, fake)

    await clickup_create_chat_channel(CreateChannelInput(workspace_id=WS, name="team"))

    body = fake.calls[0]["json_body"]
    assert body == {"name": "team"}  # no optional fields leak into the payload


def test_create_channel_user_cap() -> None:
    with pytest.raises(ValidationError):
        CreateChannelInput(name="x", user_ids=[str(i) for i in range(101)])


# --------------------------------------------------------------------------- create location channel
async def test_create_location_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "c5", "name": "Space Chat"})
    _patch(monkeypatch, fake)

    result = await clickup_create_location_chat_channel(
        CreateLocationChannelInput(workspace_id=WS, location_id="901300", location_type="space")
    )

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/chat/channels/location"
    # location is nested {id, type}; there is no top-level name field
    assert call["json_body"]["location"] == {"id": "901300", "type": "space"}
    assert "name" not in call["json_body"]
    assert "space" in result and "901300" in result


# --------------------------------------------------------------------------- create direct message
async def test_create_direct_message(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "dm1", "type": "DM"})
    _patch(monkeypatch, fake)

    result = await clickup_create_direct_message(CreateDirectMessageInput(workspace_id=WS, user_ids=["7", "8"]))

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/chat/channels/direct_message"
    assert call["json_body"]["user_ids"] == ["7", "8"]
    assert "2 participant" in result


async def test_create_direct_message_self_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "dm0"})
    _patch(monkeypatch, fake)

    result = await clickup_create_direct_message(CreateDirectMessageInput(workspace_id=WS))

    assert fake.calls[0]["json_body"] == {"user_ids": []}
    assert "Self DM" in result


def test_create_direct_message_user_cap() -> None:
    with pytest.raises(ValidationError):
        CreateDirectMessageInput(user_ids=[str(i) for i in range(16)])  # max 15


# --------------------------------------------------------------------------- get channel
async def test_get_channel_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "c1", "name": "General", "type": "CHANNEL", "topic": "hello"})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channel(GetChannelInput(workspace_id=WS, channel_id="c1"))

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/workspaces/{WS}/chat/channels/c1"
    assert call["params"]["description_format"] == "text/md"
    assert "General" in result and "hello" in result


async def test_get_channel_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"channel": {"id": "c1", "name": "Wrapped"}})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channel(GetChannelInput(workspace_id=WS, channel_id="c1", response_format="json"))

    # unwraps the "channel" key
    assert '"name": "Wrapped"' in result


# --------------------------------------------------------------------------- update channel
async def test_update_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "c1", "name": "General"})
    _patch(monkeypatch, fake)

    result = await clickup_update_chat_channel(
        UpdateChannelInput(workspace_id=WS, channel_id="c1", topic="Sprint 42", visibility="PRIVATE")
    )

    call = fake.calls[0]
    assert call["method"] == "PATCH"
    assert call["path"] == f"/workspaces/{WS}/chat/channels/c1"
    assert call["json_body"]["content_format"] == "text/md"  # default
    assert call["json_body"]["topic"] == "Sprint 42"
    assert call["json_body"]["visibility"] == "PRIVATE"
    assert "topic" in result and "visibility" in result


async def test_update_channel_location(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "c1"})
    _patch(monkeypatch, fake)

    await clickup_update_chat_channel(
        UpdateChannelInput(workspace_id=WS, channel_id="c1", location_id="L9", location_type="list")
    )

    assert fake.calls[0]["json_body"]["location"] == {"id": "L9", "type": "list"}


def test_update_channel_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        UpdateChannelInput(channel_id="c1")  # nothing to change


def test_update_channel_location_pair() -> None:
    with pytest.raises(ValidationError):
        UpdateChannelInput(channel_id="c1", location_id="L9")  # missing location_type


# --------------------------------------------------------------------------- delete channel
async def test_delete_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    _patch(monkeypatch, fake)

    result = await clickup_delete_chat_channel(DeleteChannelInput(workspace_id=WS, channel_id="c1"))

    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == f"/workspaces/{WS}/chat/channels/c1"
    assert "Deleted" in result and "c1" in result


# --------------------------------------------------------------------------- followers / members
async def test_get_followers(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"data": [{"id": "u1", "username": "Ana", "email": "ana@x.io"}], "next_cursor": "N2"})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channel_followers(ChannelMembersInput(workspace_id=WS, channel_id="c1"))

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/chat/channels/c1/followers"
    assert "Ana" in result and "ana@x.io" in result
    assert "N2" in result  # cursor surfaced


async def test_get_members_nested_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"members": [{"user": {"id": "u2", "username": "Bo"}}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channel_members(ChannelMembersInput(workspace_id=WS, channel_id="c1", cursor="C1"))

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/chat/channels/c1/members"
    assert call["params"]["cursor"] == "C1"
    assert "Bo" in result and "u2" in result  # nested {user: {...}} handled


# --------------------------------------------------------------------------- errors / edges
async def test_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    req = httpx.Request("GET", "https://api.clickup.com/api/v3/workspaces/9001/chat/channels/x")
    resp = httpx.Response(404, json={"err": "Channel not found", "ECODE": "CHAT_404"}, request=req)
    exc = httpx.HTTPStatusError("not found", request=req, response=resp)
    fake = _FakeClient(exc=exc)
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channel(GetChannelInput(workspace_id=WS, channel_id="x"))

    assert result.startswith("Error (404)")
    assert "Channel not found" in result


async def test_missing_workspace_id_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # env is isolated (no CLICKUP_TEAM_ID) by the autouse conftest fixture
    fake = _FakeClient(payload={"data": []})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_channels(GetChannelsInput())

    assert result.startswith("Error")
    assert "Workspace id" in result
    assert fake.calls == []  # never reached the client


# --------------------------------------------------------------------------- live smoke (read-only)
@pytest.mark.live
async def test_get_chat_channels_live() -> None:
    result = await clickup_get_chat_channels(GetChannelsInput(limit=10))
    assert isinstance(result, str)
    assert not result.startswith("Error")
