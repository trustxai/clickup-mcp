"""Unit tests for the ClickUp Chat message v3 tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.chat_messages import (
    MAX_OUTPUT_CHARS,
    AddChatReactionInput,
    DeleteChatMessageInput,
    DeleteChatReactionInput,
    GetChatMessageReactionsInput,
    GetChatMessageRepliesInput,
    GetChatMessagesInput,
    GetChatMessageTaggedUsersInput,
    GetChatSubtypesInput,
    SendChatMessageInput,
    SendChatReplyInput,
    UpdateChatMessageInput,
    clickup_add_chat_reaction,
    clickup_delete_chat_message,
    clickup_delete_chat_reaction,
    clickup_get_chat_message_reactions,
    clickup_get_chat_message_replies,
    clickup_get_chat_message_tagged_users,
    clickup_get_chat_messages,
    clickup_get_chat_subtypes,
    clickup_send_chat_message,
    clickup_send_chat_reply,
    clickup_update_chat_message,
)

WS = "9001"
CH = "6-901-1"
MSG = "abc123"


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
    monkeypatch.setattr("clickup_mcp.tools.chat_messages.get_client", lambda: fake)


# --------------------------------------------------------------------------- get messages
async def test_get_messages_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={"messages": [{"id": "m1", "content": "hi there", "user": {"username": "ana"}, "date": 1700000000000}]}
    )
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_messages(GetChatMessagesInput(workspace_id=WS, channel_id=CH))

    assert "hi there" in result and "m1" in result and "ana" in result
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/workspaces/{WS}/chat/channels/{CH}/messages"
    assert call["use_v3"] is True
    assert call["params"]["limit"] == 50
    assert call["params"]["content_format"] == "text/md"


async def test_get_messages_cursor_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"messages": [{"id": "m1", "content": "a"}], "next_cursor": "CUR2"})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_messages(GetChatMessagesInput(workspace_id=WS, channel_id=CH, cursor="CUR1"))

    # inbound cursor forwarded, outbound next_cursor surfaced
    assert fake.calls[0]["params"]["cursor"] == "CUR1"
    assert "CUR2" in result


async def test_get_messages_cursor_from_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    # bare-array-under-data shape with cursor nested under meta
    fake = _FakeClient(payload={"data": [{"id": "m1", "content": "x"}], "meta": {"next_cursor": "NEXT"}})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_messages(GetChatMessagesInput(workspace_id=WS, channel_id=CH))

    assert "NEXT" in result and "m1" in result


async def test_get_messages_json_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = {"id": "m1", "content": "x" * (MAX_OUTPUT_CHARS + 1000)}
    fake = _FakeClient(payload={"messages": [huge]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_messages(
        GetChatMessagesInput(workspace_id=WS, channel_id=CH, response_format="json")
    )

    assert result.endswith("…output truncated (response exceeded display cap).")


async def test_get_messages_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.clickup.com/api/v3/x")
    response = httpx.Response(404, json={"err": "Channel not found", "ECODE": "CHAT_404"}, request=request)
    fake = _FakeClient(exc=httpx.HTTPStatusError("404", request=request, response=response))
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_messages(GetChatMessagesInput(workspace_id=WS, channel_id=CH))

    assert result.startswith("Error (404)")
    assert "Channel not found" in result


async def test_get_messages_missing_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"messages": []})
    _patch(monkeypatch, fake)

    # no workspace_id and no CLICKUP_TEAM_ID (stripped by the autouse fixture)
    result = await clickup_get_chat_messages(GetChatMessagesInput(channel_id=CH))

    assert result.startswith("Error:")
    assert "No Workspace id" in result
    assert fake.calls == []


# --------------------------------------------------------------------------- send message
async def test_send_message_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"message": {"id": "new1"}})
    _patch(monkeypatch, fake)

    result = await clickup_send_chat_message(
        SendChatMessageInput(workspace_id=WS, channel_id=CH, content="Deploy green")
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{WS}/chat/channels/{CH}/messages"
    assert call["json_body"]["type"] == "message"
    assert call["json_body"]["content"] == "Deploy green"
    assert call["json_body"]["content_format"] == "text/md"
    assert "new1" in result


async def test_send_message_post_with_post_data(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "p9"})  # bare-object response shape
    _patch(monkeypatch, fake)

    result = await clickup_send_chat_message(
        SendChatMessageInput(
            workspace_id=WS,
            channel_id=CH,
            content="Kickoff",
            message_type="post",
            post_data={"subtype_id": "123"},
            followers=["u1", "u2"],
        )
    )

    body = fake.calls[0]["json_body"]
    assert body["type"] == "post"
    assert body["post_data"] == {"subtype_id": "123"}
    assert body["followers"] == ["u1", "u2"]
    assert "p9" in result


async def test_send_message_content_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        SendChatMessageInput(workspace_id=WS, channel_id=CH, content="x" * 40_001)


# --------------------------------------------------------------------------- send reply
async def test_send_reply_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"message": {"id": "r1"}})
    _patch(monkeypatch, fake)

    result = await clickup_send_chat_reply(SendChatReplyInput(workspace_id=WS, message_id=MSG, content="On it"))

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}/replies"
    assert call["json_body"]["content"] == "On it"
    assert "r1" in result and MSG in result


# --------------------------------------------------------------------------- get replies
async def test_get_replies_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"replies": [{"id": "r1", "content": "yes", "user": {"username": "bob"}}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_message_replies(GetChatMessageRepliesInput(workspace_id=WS, message_id=MSG))

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}/replies"
    assert "yes" in result and "bob" in result


# --------------------------------------------------------------------------- update message
async def test_update_message_content_and_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": MSG})
    _patch(monkeypatch, fake)

    result = await clickup_update_chat_message(
        UpdateChatMessageInput(workspace_id=WS, message_id=MSG, content="edited", resolved=True)
    )

    call = fake.calls[0]
    assert call["method"] == "PATCH"
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}"
    assert call["json_body"]["content"] == "edited"
    assert call["json_body"]["content_format"] == "text/md"
    assert call["json_body"]["post_data"] == {"resolved": True}
    assert "content" in result and "resolved" in result


async def test_update_message_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        UpdateChatMessageInput(workspace_id=WS, message_id=MSG)


# --------------------------------------------------------------------------- delete message
async def test_delete_message_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_delete_chat_message(DeleteChatMessageInput(workspace_id=WS, message_id=MSG))

    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}"
    assert "Deleted" in result and MSG in result


# --------------------------------------------------------------------------- reactions
async def test_add_reaction_lowercases(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_add_chat_reaction(
        AddChatReactionInput(workspace_id=WS, message_id=MSG, reaction="ThumbsUp")
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}/reactions"
    assert call["json_body"] == {"reaction": "thumbsup"}
    assert "thumbsup" in result


async def test_delete_reaction_path_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_delete_chat_reaction(
        DeleteChatReactionInput(workspace_id=WS, message_id=MSG, reaction="heart")
    )

    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}/reactions/heart"
    assert "heart" in result


async def test_get_reactions_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"reactions": [{"reaction": "tada", "user": {"username": "ana"}}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_message_reactions(GetChatMessageReactionsInput(workspace_id=WS, message_id=MSG))

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}/reactions"
    assert "tada" in result and "ana" in result
    # reactions endpoint takes no content_format
    assert "content_format" not in call["params"]


# --------------------------------------------------------------------------- tagged users
async def test_get_tagged_users_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"tagged_users": [{"id": 42, "username": "carol"}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_message_tagged_users(
        GetChatMessageTaggedUsersInput(workspace_id=WS, message_id=MSG)
    )

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/chat/messages/{MSG}/tagged_users"
    assert "carol" in result and "42" in result


# --------------------------------------------------------------------------- subtypes
async def test_get_subtypes_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"subtypes": [{"id": "s1", "name": "Announcement"}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_subtypes(GetChatSubtypesInput(workspace_id=WS, comment_type="post"))

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/workspaces/{WS}/comments/types/post/subtypes"
    assert call["use_v3"] is True
    assert "Announcement" in result and "s1" in result


async def test_get_subtypes_json_bare_array(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[{"id": "s1", "name": "Idea"}])  # bare-array response shape
    _patch(monkeypatch, fake)

    result = await clickup_get_chat_subtypes(
        GetChatSubtypesInput(workspace_id=WS, comment_type="ai", response_format="json")
    )

    assert '"count": 1' in result and "Idea" in result
    assert fake.calls[0]["path"] == f"/workspaces/{WS}/comments/types/ai/subtypes"
