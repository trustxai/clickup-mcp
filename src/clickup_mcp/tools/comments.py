"""Comments tool module — task/list/Chat-view comments, threaded replies, update/delete.

Wave 2 — task t7-comments (inventory I, 10 eps). Mirrors `tools/health.py`'s shape:
`@mcp.tool` + `ToolAnnotations`, one pydantic input model per tool, structured
docstrings, `-> str` returns, `try/except -> handle_api_error`.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Context-window guard independent of ClickUp's own comment paging (which is
# fixed at 25/page and not adjustable via a `limit` param).
MAX_DISPLAY_COMMENTS = 25
MAX_COMMENT_TEXT_CHARS = 400


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _comment_body(
    comment_text: str,
    *,
    assignee: int | None = None,
    group_assignee: str | None = None,
    notify_all: bool = False,
) -> dict[str, Any]:
    """Build the JSON body shared by every comment-creation endpoint.

    ClickUp also accepts a structured `comment` array of rich-text blocks
    (bold/italic/links/mentions) instead of plain `comment_text`; this module
    exposes only the simpler `comment_text` string, which covers the vast
    majority of use cases and is far friendlier for an LLM caller to produce.
    """
    body: dict[str, Any] = {"comment_text": comment_text, "notify_all": notify_all}
    if assignee is not None:
        body["assignee"] = assignee
    if group_assignee is not None:
        body["group_assignee"] = group_assignee
    return body


def _task_id_query(custom_task_ids: bool, team_id: str | None) -> dict[str, Any]:
    """Build the `custom_task_ids`/`team_id` query pair used by task-comment endpoints."""
    if not custom_task_ids:
        return {}
    query: dict[str, Any] = {"custom_task_ids": True}
    if team_id:
        query["team_id"] = team_id
    return query


def _cursor_query(start: int | None, start_id: str | None) -> dict[str, Any]:
    """Build the `start`/`start_id` cursor-pagination query pair."""
    query: dict[str, Any] = {}
    if start is not None:
        query["start"] = start
    if start_id is not None:
        query["start_id"] = start_id
    return query


def _created_confirmation(kind: str, resp_body: dict[str, Any], where: str) -> str:
    """Render a human confirmation string for a create-style mutation."""
    comment_id = resp_body.get("id", "unknown")
    hist_id = resp_body.get("hist_id")
    date = epoch_to_human(resp_body.get("date"))
    extra = f", hist_id **{hist_id}**" if hist_id else ""
    return f"Created {kind} **{comment_id}**{extra} on {where} at {date}."


def _format_comment(comment: dict[str, Any]) -> str:
    """Render one comment as a single markdown bullet."""
    comment_id = comment.get("id", "unknown")
    user = comment.get("user") or {}
    username = user.get("username") or user.get("email") or "unknown"
    date = epoch_to_human(comment.get("date"))
    text = str(comment.get("comment_text") or "").strip()
    if len(text) > MAX_COMMENT_TEXT_CHARS:
        text = text[:MAX_COMMENT_TEXT_CHARS] + "…"

    resolved_note = " _(resolved)_" if comment.get("resolved") else ""

    assignee = comment.get("assignee")
    assignee_note = ""
    if isinstance(assignee, dict) and assignee:
        assignee_name = assignee.get("username") or assignee.get("email") or assignee.get("id")
        assignee_note = f" — assigned to **{assignee_name}**"

    reply_count = comment.get("reply_count")
    reply_note = f" ({reply_count} repl{'y' if reply_count == 1 else 'ies'})" if reply_count else ""

    return (
        f"- **#{comment_id}** by **{username}** ({date}){resolved_note}{assignee_note}{reply_note}: {text or '_empty_'}"
    )


def _render_comments(comments: list[dict[str, Any]], *, fmt: ResponseFormat, title: str, cursor_paged: bool) -> str:
    """Uniform markdown/JSON rendering for a page of comments.

    Unlike `formatters.paginated_response` (built for limit/offset paging),
    ClickUp comments page via an opaque `start`/`start_id` cursor and never
    report a `total`, so this module renders its own page summary and — when
    a full page (25) comes back — surfaces the cursor values for the next call.
    """
    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "count": len(comments), "comments": comments})

    display = comments[:MAX_DISPLAY_COMMENTS]
    lines = [f"# {title}", "", f"Showing **{len(display):,}** comment(s)."]
    if cursor_paged and len(comments) >= MAX_DISPLAY_COMMENTS and comments:
        oldest = comments[-1]
        lines.append(
            f"Full page returned — more (older) comments may exist. Call again with "
            f"start={oldest.get('date')!r} and start_id={oldest.get('id')!r} to page backward."
        )
    lines.append("")
    if display:
        lines.extend(_format_comment(c) for c in display)
    else:
        lines.append("_No comments._")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Task comments
# --------------------------------------------------------------------------


class CreateTaskCommentInput(BaseModel):
    """Input for `clickup_create_task_comment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task to comment on.")
    comment_text: str = Field(..., min_length=1, description="Plain-text/markdown-like body of the comment.")
    assignee: int | None = Field(default=None, description="User id to assign this comment to as an action item.")
    group_assignee: str | None = Field(
        default=None, description="User group id to assign this comment to instead of a single user."
    )
    notify_all: bool = Field(
        default=False,
        description="If true, the comment's creator is also notified. Assignees/watchers are always notified.",
    )
    custom_task_ids: bool = Field(
        default=False, description="Set true to interpret task_id as a custom task id instead of ClickUp's internal id."
    )
    team_id: str | None = Field(default=None, description="Workspace (team) id; required when custom_task_ids is true.")


@mcp.tool(
    name="clickup_create_task_comment",
    annotations=ToolAnnotations(
        title="Create Task Comment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_task_comment(params: CreateTaskCommentInput) -> str:
    """Add a comment to a task.

    Posts to `POST /task/{task_id}/comment`.

    When to Use:
    - To leave feedback, ask a question, or hand off context on a task.
    - To assign a follow-up action item via `assignee`/`group_assignee`.

    When NOT to Use:
    - To reply inside an existing comment thread — use `clickup_create_threaded_comment`.
    - To comment on a List's info panel or a Chat view — use
      `clickup_create_list_comment` / `clickup_create_chat_view_comment`.

    Returns:
    A confirmation string with the new comment's id, hist_id, and timestamp, or
    an `Error ...` string on failure.

    Examples:
    params = {"task_id": "abc123", "comment_text": "Blocked on design review.", "notify_all": True}

    Error Handling:
    404 means the task id is wrong; 403 can mean you lack comment access on the task.
    """
    try:
        client = get_client()
        query = _task_id_query(params.custom_task_ids, params.team_id)
        body = _comment_body(
            params.comment_text,
            assignee=params.assignee,
            group_assignee=params.group_assignee,
            notify_all=params.notify_all,
        )
        resp = await client.request("POST", f"/task/{params.task_id}/comment", params=query, json_body=body)
        return _created_confirmation("comment", resp.json(), f"task **{params.task_id}**")
    except Exception as exc:
        return handle_api_error(exc)


class GetTaskCommentsInput(BaseModel):
    """Input for `clickup_get_task_comments`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task whose comments to list.")
    custom_task_ids: bool = Field(
        default=False, description="Set true to interpret task_id as a custom task id instead of ClickUp's internal id."
    )
    team_id: str | None = Field(default=None, description="Workspace (team) id; required when custom_task_ids is true.")
    start: int | None = Field(
        default=None,
        description="Cursor: unix-ms timestamp of the oldest comment from a previous page. Pair with start_id.",
    )
    start_id: str | None = Field(default=None, description="Cursor: comment id paired with start.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown or json."
    )


@mcp.tool(
    name="clickup_get_task_comments",
    annotations=ToolAnnotations(
        title="Get Task Comments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_task_comments(params: GetTaskCommentsInput) -> str:
    """List comments on a task, newest first.

    Calls `GET /task/{task_id}/comment`.

    When to Use:
    - To read discussion history on a task before acting on it.

    When NOT to Use:
    - To read replies inside a specific thread — use `clickup_get_threaded_comments`.

    Returns:
    Markdown (default) or JSON list of comments with id, author, date, resolved
    state, assignee, and (truncated) text.

    Pagination:
    Cursor-based, NOT limit/offset. Omit `start`/`start_id` for the most recent
    25 comments (ClickUp's fixed page size — not configurable). To page to
    OLDER comments, pass the oldest comment's `date` as `start` and its `id` as
    `start_id`; the markdown output surfaces both values whenever a full page
    (25) comes back, so callers can loop until a short page signals the end.

    Examples:
    params = {"task_id": "abc123"}
    params = {"task_id": "abc123", "start": 1508369194377, "start_id": "446750"}

    Error Handling:
    404 means the task id is wrong.
    """
    try:
        client = get_client()
        query = {
            **_task_id_query(params.custom_task_ids, params.team_id),
            **_cursor_query(params.start, params.start_id),
        }
        resp = await client.request("GET", f"/task/{params.task_id}/comment", params=query)
        comments = resp.json().get("comments", [])
        return _render_comments(
            comments, fmt=params.response_format, title=f"Comments on task {params.task_id}", cursor_paged=True
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# List comments
# --------------------------------------------------------------------------


class CreateListCommentInput(BaseModel):
    """Input for `clickup_create_list_comment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., min_length=1, description="ID of the list to comment on.")
    comment_text: str = Field(..., min_length=1, description="Plain-text/markdown-like body of the comment.")
    assignee: int | None = Field(default=None, description="User id to assign this comment to.")
    notify_all: bool = Field(
        default=False,
        description="If true, the comment's creator is also notified. Assignees/watchers are always notified.",
    )


@mcp.tool(
    name="clickup_create_list_comment",
    annotations=ToolAnnotations(
        title="Create List Comment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_list_comment(params: CreateListCommentInput) -> str:
    """Add a comment to a List's info panel.

    Posts to `POST /list/{list_id}/comment`.

    When to Use:
    - To leave a note on the List itself (not on an individual task) — e.g. a
      status update visible to everyone with access to the List.

    When NOT to Use:
    - To comment on a specific task — use `clickup_create_task_comment`.

    Returns:
    A confirmation string with the new comment's id, hist_id, and timestamp, or
    an `Error ...` string on failure.

    Examples:
    params = {"list_id": "901234", "comment_text": "Sprint scope finalized.", "notify_all": False}

    Error Handling:
    404 means the list id is wrong; 403 can mean you lack comment access on the list.
    """
    try:
        client = get_client()
        body = _comment_body(params.comment_text, assignee=params.assignee, notify_all=params.notify_all)
        resp = await client.request("POST", f"/list/{params.list_id}/comment", json_body=body)
        return _created_confirmation("comment", resp.json(), f"list **{params.list_id}**")
    except Exception as exc:
        return handle_api_error(exc)


class GetListCommentsInput(BaseModel):
    """Input for `clickup_get_list_comments`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., min_length=1, description="ID of the list whose comments to list.")
    start: int | None = Field(
        default=None,
        description="Cursor: unix-ms timestamp of the oldest comment from a previous page. Pair with start_id.",
    )
    start_id: str | None = Field(default=None, description="Cursor: comment id paired with start.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown or json."
    )


@mcp.tool(
    name="clickup_get_list_comments",
    annotations=ToolAnnotations(
        title="Get List Comments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_list_comments(params: GetListCommentsInput) -> str:
    """List comments on a List's info panel, newest first.

    Calls `GET /list/{list_id}/comment`.

    When to Use:
    - To read the List-level discussion/status history.

    When NOT to Use:
    - To read comments on a specific task — use `clickup_get_task_comments`.

    Returns:
    Markdown (default) or JSON list of comments with id, author, date, resolved
    state, assignee, and (truncated) text.

    Pagination:
    Cursor-based (`start`/`start_id`), identical pattern to
    `clickup_get_task_comments` — see that tool's docstring for the full
    explanation. Omit both for the most recent 25 comments.

    Examples:
    params = {"list_id": "901234"}

    Error Handling:
    404 means the list id is wrong.
    """
    try:
        client = get_client()
        query = _cursor_query(params.start, params.start_id)
        resp = await client.request("GET", f"/list/{params.list_id}/comment", params=query)
        comments = resp.json().get("comments", [])
        return _render_comments(
            comments, fmt=params.response_format, title=f"Comments on list {params.list_id}", cursor_paged=True
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# Chat view comments
# --------------------------------------------------------------------------


class CreateChatViewCommentInput(BaseModel):
    """Input for `clickup_create_chat_view_comment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    view_id: str = Field(..., min_length=1, description="ID of the Chat view to post into.")
    comment_text: str = Field(..., min_length=1, description="Plain-text/markdown-like body of the message.")
    notify_all: bool = Field(
        default=False,
        description="If true, the comment's creator is also notified. Assignees/watchers are always notified.",
    )


@mcp.tool(
    name="clickup_create_chat_view_comment",
    annotations=ToolAnnotations(
        title="Create Chat View Comment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_chat_view_comment(params: CreateChatViewCommentInput) -> str:
    """Post a comment into a Chat view.

    Posts to `POST /view/{view_id}/comment`. This is the legacy Chat-view
    comment surface (views of `type=conversation`); the newer, richer Chat API
    (channels + messages, all v3) lives in `tools/chat_messages.py` — prefer
    that module for new Chat integrations, and this tool only when you are
    already working with a Chat-type view id.

    When to Use:
    - To post into an existing Chat view when you already have its view_id.

    When NOT to Use:
    - To send a message in a modern Chat channel — use
      `clickup_send_chat_message` (`tools/chat_messages.py`) instead.

    Returns:
    A confirmation string with the new comment's id, hist_id, and timestamp, or
    an `Error ...` string on failure.

    Examples:
    params = {"view_id": "105", "comment_text": "Standup notes for today."}

    Error Handling:
    404 means the view id is wrong or is not a Chat-type view.
    """
    try:
        client = get_client()
        body = _comment_body(params.comment_text, notify_all=params.notify_all)
        resp = await client.request("POST", f"/view/{params.view_id}/comment", json_body=body)
        return _created_confirmation("comment", resp.json(), f"Chat view **{params.view_id}**")
    except Exception as exc:
        return handle_api_error(exc)


class GetChatViewCommentsInput(BaseModel):
    """Input for `clickup_get_chat_view_comments`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    view_id: str = Field(..., min_length=1, description="ID of the Chat view whose comments to list.")
    start: int | None = Field(
        default=None,
        description="Cursor: unix-ms timestamp of the oldest comment from a previous page. Pair with start_id.",
    )
    start_id: str | None = Field(default=None, description="Cursor: comment id paired with start.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown or json."
    )


@mcp.tool(
    name="clickup_get_chat_view_comments",
    annotations=ToolAnnotations(
        title="Get Chat View Comments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_view_comments(params: GetChatViewCommentsInput) -> str:
    """List comments in a Chat view, newest first.

    Calls `GET /view/{view_id}/comment`.

    When to Use:
    - To read the message history of a legacy Chat-type view.

    When NOT to Use:
    - To read messages in a modern Chat channel — use
      `clickup_get_chat_channel_messages` (`tools/chat_messages.py`) instead.

    Returns:
    Markdown (default) or JSON list of comments with id, author, date, and
    (truncated) text.

    Pagination:
    Cursor-based (`start`/`start_id`), identical pattern to
    `clickup_get_task_comments` — see that tool's docstring for the full
    explanation. Omit both for the most recent 25 comments.

    Examples:
    params = {"view_id": "105"}

    Error Handling:
    404 means the view id is wrong or is not a Chat-type view.
    """
    try:
        client = get_client()
        query = _cursor_query(params.start, params.start_id)
        resp = await client.request("GET", f"/view/{params.view_id}/comment", params=query)
        comments = resp.json().get("comments", [])
        return _render_comments(
            comments, fmt=params.response_format, title=f"Comments in Chat view {params.view_id}", cursor_paged=True
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# Update / delete (comment_id addressed directly, resource-agnostic)
# --------------------------------------------------------------------------


class UpdateCommentInput(BaseModel):
    """Input for `clickup_update_comment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment_id: str = Field(..., min_length=1, description="ID of the comment to update.")
    comment_text: str = Field(..., min_length=1, description="Full replacement text for the comment.")
    assignee: int | None = Field(default=None, description="User id to (re)assign this comment to.")
    group_assignee: str | None = Field(default=None, description="User group id to (re)assign this comment to.")
    resolved: bool | None = Field(default=None, description="Mark the comment resolved (true) or unresolved (false).")


@mcp.tool(
    name="clickup_update_comment",
    annotations=ToolAnnotations(
        title="Update Comment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_comment(params: UpdateCommentInput) -> str:
    """Edit a comment's text, (re)assign it, or toggle its resolved state.

    Calls `PUT /comment/{comment_id}`. Works uniformly on task, List, Chat-view,
    and threaded-reply comments — they all share the same `comment_id` space.

    When to Use:
    - To fix a typo, add detail, reassign a comment's action item, or mark it
      resolved once addressed.

    When NOT to Use:
    - To remove a comment entirely — use `clickup_delete_comment`.
    - To reply within a thread rather than editing — use
      `clickup_create_threaded_comment`.

    Returns:
    A confirmation string naming the updated comment, or an `Error ...` string
    on failure.

    Examples:
    params = {"comment_id": "446750", "comment_text": "Resolved — see PR #42.", "resolved": True}

    Error Handling:
    404 means the comment id is wrong; 403 can mean you don't own the comment
    and lack edit permission.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"comment_text": params.comment_text}
        if params.assignee is not None:
            body["assignee"] = params.assignee
        if params.group_assignee is not None:
            body["group_assignee"] = params.group_assignee
        if params.resolved is not None:
            body["resolved"] = params.resolved
        await client.request("PUT", f"/comment/{params.comment_id}", json_body=body)
        return f"Updated comment **{params.comment_id}**."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteCommentInput(BaseModel):
    """Input for `clickup_delete_comment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment_id: str = Field(..., min_length=1, description="ID of the comment to permanently delete.")


@mcp.tool(
    name="clickup_delete_comment",
    annotations=ToolAnnotations(
        title="Delete Comment",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_comment(params: DeleteCommentInput) -> str:
    """Permanently delete a comment.

    Calls `DELETE /comment/{comment_id}`. Works uniformly on task, List,
    Chat-view, and threaded-reply comments.

    When to Use:
    - To remove a comment that was posted in error or is no longer relevant.

    When NOT to Use:
    - To just fix wording or mark it resolved — use `clickup_update_comment`
      instead of destroying history.

    Returns:
    A confirmation string, or an `Error ...` string on failure.

    Examples:
    params = {"comment_id": "446750"}

    Error Handling:
    404 means the comment id is wrong or was already deleted.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/comment/{params.comment_id}")
        return f"Deleted comment **{params.comment_id}**."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# Threaded comments (replies)
# --------------------------------------------------------------------------


class CreateThreadedCommentInput(BaseModel):
    """Input for `clickup_create_threaded_comment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment_id: str = Field(..., min_length=1, description="ID of the parent comment to reply to.")
    comment_text: str = Field(..., min_length=1, description="Plain-text/markdown-like body of the reply.")
    assignee: int | None = Field(default=None, description="User id to assign this reply to.")
    group_assignee: str | None = Field(default=None, description="User group id to assign this reply to.")
    notify_all: bool = Field(
        default=False,
        description="If true, the comment's creator is also notified. Assignees/watchers are always notified.",
    )


@mcp.tool(
    name="clickup_create_threaded_comment",
    annotations=ToolAnnotations(
        title="Create Threaded Comment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_threaded_comment(params: CreateThreadedCommentInput) -> str:
    """Reply inside an existing comment's thread.

    Posts to `POST /comment/{comment_id}/reply`. Threaded replies keep a
    discussion nested under its parent comment instead of scattering related
    follow-ups as separate top-level task/list/Chat-view comments.

    When to Use:
    - To respond directly to a specific comment (task, List, or Chat-view)
      rather than starting a new top-level comment.

    When NOT to Use:
    - To start a new top-level comment — use `clickup_create_task_comment` /
      `clickup_create_list_comment` / `clickup_create_chat_view_comment`.

    Returns:
    A confirmation string with the new reply's id, hist_id, and timestamp, or
    an `Error ...` string on failure.

    Examples:
    params = {"comment_id": "446750", "comment_text": "Agreed, I'll update the estimate."}

    Error Handling:
    404 means the parent comment_id is wrong.
    """
    try:
        client = get_client()
        body = _comment_body(
            params.comment_text,
            assignee=params.assignee,
            group_assignee=params.group_assignee,
            notify_all=params.notify_all,
        )
        resp = await client.request("POST", f"/comment/{params.comment_id}/reply", json_body=body)
        return _created_confirmation("reply", resp.json(), f"comment **{params.comment_id}**")
    except Exception as exc:
        return handle_api_error(exc)


class GetThreadedCommentsInput(BaseModel):
    """Input for `clickup_get_threaded_comments`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment_id: str = Field(..., min_length=1, description="ID of the parent comment whose replies to list.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown or json."
    )


@mcp.tool(
    name="clickup_get_threaded_comments",
    annotations=ToolAnnotations(
        title="Get Threaded Comments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_threaded_comments(params: GetThreadedCommentsInput) -> str:
    """List the replies in a comment's thread.

    Calls `GET /comment/{comment_id}/reply`. The parent comment itself is NOT
    included in the response — only its replies.

    When to Use:
    - To read the full discussion nested under a specific comment.

    When NOT to Use:
    - To read a task/list/Chat-view's top-level comments — use
      `clickup_get_task_comments` / `clickup_get_list_comments` /
      `clickup_get_chat_view_comments`.

    Returns:
    Markdown (default) or JSON list of replies with id, author, date, and
    (truncated) text.

    Pagination:
    Unlike the three top-level comment-listing tools, ClickUp does NOT expose
    `start`/`start_id` cursor pagination for replies — this endpoint returns
    the full thread in one call, so no cursor params are surfaced here.

    Examples:
    params = {"comment_id": "446750"}

    Error Handling:
    404 means the parent comment_id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/comment/{params.comment_id}/reply")
        replies = resp.json().get("comments", [])
        return _render_comments(
            replies, fmt=params.response_format, title=f"Replies to comment {params.comment_id}", cursor_paged=False
        )
    except Exception as exc:
        return handle_api_error(exc)
