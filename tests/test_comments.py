"""Unit tests for the comments tool module against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.comments import (
    CreateChatViewCommentInput,
    CreateListCommentInput,
    CreateTaskCommentInput,
    CreateThreadedCommentInput,
    DeleteCommentInput,
    GetChatViewCommentsInput,
    GetListCommentsInput,
    GetTaskCommentsInput,
    GetThreadedCommentsInput,
    UpdateCommentInput,
    clickup_create_chat_view_comment,
    clickup_create_list_comment,
    clickup_create_task_comment,
    clickup_create_threaded_comment,
    clickup_delete_comment,
    clickup_get_chat_view_comments,
    clickup_get_list_comments,
    clickup_get_task_comments,
    clickup_get_threaded_comments,
    clickup_update_comment,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Records every call so tests can assert (method, path, params, json_body)."""

    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload or {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        **_: Any,
    ) -> _FakeResponse:
        self.calls.append((method, path, params, json_body))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _sample_comment(**overrides: Any) -> dict[str, Any]:
    comment: dict[str, Any] = {
        "id": "446750",
        "comment_text": "Blocked on design review.",
        "user": {"id": 183, "username": "Alejandro", "email": "alej@example.com"},
        "resolved": False,
        "assignee": None,
        "date": "1567780450202",
    }
    comment.update(overrides)
    return comment


# --------------------------------------------------------------------------
# Task comments
# --------------------------------------------------------------------------


async def test_create_task_comment_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "446750", "hist_id": "hist-1", "date": 1567780450202})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_create_task_comment(
        CreateTaskCommentInput(task_id="abc123", comment_text="Blocked on design review.", notify_all=True)
    )

    assert "Created comment **446750**" in result
    assert "task **abc123**" in result
    method, path, params, body = fake.calls[0]
    assert method == "POST"
    assert path == "/task/abc123/comment"
    assert params == {}
    assert body == {"comment_text": "Blocked on design review.", "notify_all": True}


async def test_create_task_comment_with_custom_task_ids_and_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "1", "date": 1})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    await clickup_create_task_comment(
        CreateTaskCommentInput(
            task_id="CUSTOM-1",
            comment_text="hi",
            assignee=183,
            group_assignee="grp-1",
            custom_task_ids=True,
            team_id="900",
        )
    )

    _, _, params, body = fake.calls[0]
    assert params == {"custom_task_ids": True, "team_id": "900"}
    assert body == {"comment_text": "hi", "notify_all": False, "assignee": 183, "group_assignee": "grp-1"}


async def test_create_task_comment_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        exc=httpx.HTTPStatusError("nope", request=httpx.Request("POST", "http://x"), response=httpx.Response(404))
    )
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_create_task_comment(CreateTaskCommentInput(task_id="missing", comment_text="hi"))

    assert result.startswith("Error (404)")


async def test_get_task_comments_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment()]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_task_comments(GetTaskCommentsInput(task_id="abc123"))

    assert "Comments on task abc123" in result
    assert "#446750" in result
    assert "Alejandro" in result
    method, path, params, _ = fake.calls[0]
    assert method == "GET"
    assert path == "/task/abc123/comment"
    assert params == {}


async def test_get_task_comments_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment()]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_task_comments(
        GetTaskCommentsInput(task_id="abc123", response_format=ResponseFormat.JSON)
    )

    assert '"comments"' in result
    assert "446750" in result


async def test_get_task_comments_cursor_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment(id=str(i), date=str(i)) for i in range(25)]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_task_comments(
        GetTaskCommentsInput(task_id="abc123", start=1567780450202, start_id="446750")
    )

    _, _, params, _ = fake.calls[0]
    assert params == {"start": 1567780450202, "start_id": "446750"}
    # a full 25-comment page should surface the next cursor for the caller.
    assert "start=" in result
    assert "start_id=" in result


async def test_get_task_comments_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": []})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_task_comments(GetTaskCommentsInput(task_id="abc123"))

    assert "_No comments._" in result


# --------------------------------------------------------------------------
# List comments
# --------------------------------------------------------------------------


async def test_create_list_comment_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "1", "hist_id": "h1", "date": 1})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_create_list_comment(
        CreateListCommentInput(list_id="901234", comment_text="Sprint scope finalized.")
    )

    assert "Created comment **1**" in result
    assert "list **901234**" in result
    method, path, params, body = fake.calls[0]
    assert method == "POST"
    assert path == "/list/901234/comment"
    assert body == {"comment_text": "Sprint scope finalized.", "notify_all": False}


async def test_get_list_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment()]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_list_comments(GetListCommentsInput(list_id="901234"))

    assert "Comments on list 901234" in result
    method, path, _, _ = fake.calls[0]
    assert method == "GET"
    assert path == "/list/901234/comment"


async def test_get_list_comments_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("refused"))
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_list_comments(GetListCommentsInput(list_id="901234"))

    assert result.startswith("Error")
    assert "could not connect" in result


# --------------------------------------------------------------------------
# Chat view comments
# --------------------------------------------------------------------------


async def test_create_chat_view_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "9", "date": 1})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_create_chat_view_comment(
        CreateChatViewCommentInput(view_id="105", comment_text="Standup notes.")
    )

    assert "Chat view **105**" in result
    method, path, _, body = fake.calls[0]
    assert method == "POST"
    assert path == "/view/105/comment"
    assert body == {"comment_text": "Standup notes.", "notify_all": False}


async def test_get_chat_view_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment()]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_chat_view_comments(GetChatViewCommentsInput(view_id="105"))

    assert "Comments in Chat view 105" in result
    method, path, _, _ = fake.calls[0]
    assert path == "/view/105/comment"


# --------------------------------------------------------------------------
# Update / delete
# --------------------------------------------------------------------------


async def test_update_comment_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_update_comment(
        UpdateCommentInput(comment_id="446750", comment_text="Resolved — see PR #42.", resolved=True)
    )

    assert "Updated comment **446750**" in result
    method, path, _, body = fake.calls[0]
    assert method == "PUT"
    assert path == "/comment/446750"
    assert body == {"comment_text": "Resolved — see PR #42.", "resolved": True}


async def test_update_comment_omits_unset_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    await clickup_update_comment(UpdateCommentInput(comment_id="1", comment_text="just text"))

    _, _, _, body = fake.calls[0]
    assert body == {"comment_text": "just text"}


async def test_delete_comment_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_delete_comment(DeleteCommentInput(comment_id="446750"))

    assert "Deleted comment **446750**" in result
    method, path, _, _ = fake.calls[0]
    assert method == "DELETE"
    assert path == "/comment/446750"


async def test_delete_comment_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        exc=httpx.HTTPStatusError("nope", request=httpx.Request("DELETE", "http://x"), response=httpx.Response(404))
    )
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_delete_comment(DeleteCommentInput(comment_id="missing"))

    assert result.startswith("Error (404)")


# --------------------------------------------------------------------------
# Threaded comments
# --------------------------------------------------------------------------


async def test_create_threaded_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "77", "hist_id": "h77", "date": 1})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_create_threaded_comment(
        CreateThreadedCommentInput(comment_id="446750", comment_text="Agreed, updating the estimate.")
    )

    assert "Created reply **77**" in result
    assert "comment **446750**" in result
    method, path, _, body = fake.calls[0]
    assert method == "POST"
    assert path == "/comment/446750/reply"
    assert body == {"comment_text": "Agreed, updating the estimate.", "notify_all": False}


async def test_get_threaded_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment(id="99")]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_threaded_comments(GetThreadedCommentsInput(comment_id="446750"))

    assert "Replies to comment 446750" in result
    assert "#99" in result
    method, path, params, _ = fake.calls[0]
    assert method == "GET"
    assert path == "/comment/446750/reply"
    assert params is None


async def test_get_threaded_comments_no_cursor_hint_even_on_full_page(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"comments": [_sample_comment(id=str(i)) for i in range(25)]})
    monkeypatch.setattr("clickup_mcp.tools.comments.get_client", lambda: fake)

    result = await clickup_get_threaded_comments(GetThreadedCommentsInput(comment_id="446750"))

    # Replies are not cursor-paginated, so no "page backward" hint should appear
    # even though a full 25-item page came back.
    assert "page backward" not in result


# --------------------------------------------------------------------------
# Extra input-model validation coverage
# --------------------------------------------------------------------------


def test_input_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CreateTaskCommentInput(task_id="a", comment_text="hi", unexpected="nope")  # type: ignore[call-arg]
