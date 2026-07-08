"""ClickUp Chat tools — messages, replies, and reactions (API v3).

Everything here lives on the ClickUp v3 Chat surface, so every request goes
through ``client.request(..., use_v3=True)``. Two path families are used:

- Channel-scoped:  ``/workspaces/{workspace_id}/chat/channels/{channel_id}/messages``
  (list a channel's messages, send a new message).
- Message-scoped:  ``/workspaces/{workspace_id}/chat/messages/{message_id}/...``
  (update, delete, replies, reactions, tagged users).

`clickup_get_chat_subtypes` is the odd one out: post subtype IDs live under the
comments surface (``/workspaces/{workspace_id}/comments/types/{comment_type}/subtypes``),
not under ``/chat``.

Conventions shared with the other v3 modules (Docs, Chat channels):
- **Cursor** pagination is asymmetric — you *send* a `cursor` query param and the
  response carries a `next_cursor`; loop until it is empty.
- `content_format` is `text/md` (default) or `text/plain`.
- v3 responses are parsed defensively (`_extract_*`) because ClickUp is loose about
  bare-array vs keyed-object (`data`/`messages`/`items`) and top-level vs `meta`
  cursor placement.
- Reactions are lower-case emoji names (e.g. `thumbsup`, `heart`).
"""

from __future__ import annotations

import urllib.parse
from enum import StrEnum
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Display / byte guards independent of API paging, to stay under the 1 MB MCP limit.
MAX_DISPLAY_ITEMS = 50
MAX_OUTPUT_CHARS = 800_000
MAX_CONTENT_CHARS = 40_000  # ClickUp caps message content at 40k characters.


class MessageType(StrEnum):
    """Kind of chat message being created."""

    MESSAGE = "message"
    POST = "post"


class ContentFormat(StrEnum):
    """Format of message content on the wire."""

    MARKDOWN = "text/md"
    PLAIN = "text/plain"


class CommentType(StrEnum):
    """Comment/post families that expose subtype IDs."""

    POST = "post"
    AI = "ai"
    SYNCUP = "syncup"
    AI_VIA_BRAIN = "ai_via_brain"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_workspace_id(workspace_id: str | None) -> str:
    """Return the caller's Workspace id, else the configured default team id."""
    resolved = (workspace_id or "").strip() or get_settings().clickup_team_id.strip()
    if not resolved:
        raise ValueError(
            "No Workspace id available. Pass workspace_id, or set CLICKUP_TEAM_ID "
            "(your Workspace/team id) in the environment or .env."
        )
    return resolved


def _cap(text: str) -> str:
    """Trim oversized output so a single response cannot blow the MCP size limit."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n\n…output truncated (response exceeded display cap)."


def _extract_list(payload: Any, *keys: str) -> tuple[list[dict[str, Any]], str | None]:
    """Normalize a v3 list response into (items, next_cursor).

    ClickUp v3 returns either a bare array or an object keyed by one of `keys`
    (falling back to `data`/`items`), and the pagination cursor lives either at
    the top level or under `meta`.
    """
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        items: list[dict[str, Any]] | None = None
        for key in (*keys, "data", "items"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        cursor = payload.get("next_cursor") or payload.get("cursor")
        meta = payload.get("meta")
        if not cursor and isinstance(meta, dict):
            cursor = meta.get("next_cursor") or meta.get("cursor")
        return list(items or []), cursor
    return [], None


def _extract_object(payload: Any, *keys: str) -> dict[str, Any]:
    """Pull the single resource out of a v3 mutation response (object or {key: object})."""
    if isinstance(payload, dict):
        for key in (*keys, "data"):
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                return candidate
        return payload
    return {}


def _author_of(item: dict[str, Any]) -> str:
    """Best-effort display name for a message/reaction author."""
    user = item.get("user") or item.get("author") or item.get("created_by")
    if isinstance(user, dict):
        return str(user.get("username") or user.get("email") or user.get("id") or "unknown")
    for key in ("user_id", "userid", "author_id"):
        if item.get(key) is not None:
            return str(item[key])
    return "unknown"


def _message_timestamp(item: dict[str, Any]) -> Any:
    return item.get("date") or item.get("date_created") or item.get("created_at")


def _format_message(msg: dict[str, Any]) -> str:
    mid = msg.get("id", "?")
    author = _author_of(msg)
    when = _message_timestamp(msg)
    content = str(msg.get("content") or msg.get("text") or "").strip()
    if len(content) > 500:
        content = content[:500] + "…"
    head = f"- **{author}** · msg `{mid}`"
    if when:
        head += f" · {epoch_to_human(when)}"
    reply_count = msg.get("reply_count") or msg.get("replies_count")
    if reply_count:
        head += f" · {reply_count} repl{'y' if reply_count == 1 else 'ies'}"
    body = f"\n  {content}" if content else ""
    return head + body


def _format_reaction(reaction: dict[str, Any]) -> str:
    name = reaction.get("reaction") or reaction.get("name") or reaction.get("emoji") or "?"
    who = _author_of(reaction)
    return f"- `{name}` by {who}"


def _format_tagged_user(user: dict[str, Any]) -> str:
    name = user.get("username") or user.get("email") or "unknown"
    uid = user.get("id", "?")
    return f"- **{name}** (id `{uid}`)"


def _format_subtype(subtype: dict[str, Any]) -> str:
    name = subtype.get("name") or "(unnamed)"
    sid = subtype.get("id", "?")
    return f"- **{name}** (id `{sid}`)"


def _render_list(
    *,
    items: list[dict[str, Any]],
    next_cursor: str | None,
    fmt: ResponseFormat,
    json_key: str,
    title: str,
    item_formatter: Any,
    empty_note: str,
) -> str:
    """Shared markdown/JSON rendering for the cursor-paginated read tools."""
    if fmt is ResponseFormat.JSON:
        return _cap(to_json({"count": len(items), "next_cursor": next_cursor, json_key: items}))
    shown = items[:MAX_DISPLAY_ITEMS]
    lines = [f"# {title} ({len(items)})", ""]
    if len(items) > len(shown):
        lines.append(f"_Showing first {len(shown)}._")
    for item in shown:
        lines.append(item_formatter(item))
    if not items:
        lines.append(empty_note)
    if next_cursor:
        lines.append("")
        lines.append(f"More available — call again with `cursor` = `{next_cursor}`.")
    return _cap("\n".join(lines))


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class _WorkspaceMixin(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    workspace_id: str | None = Field(
        default=None,
        description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted.",
    )


class GetChatMessagesInput(_WorkspaceMixin):
    channel_id: str = Field(min_length=1, description="Id of the Chat channel whose messages to list.")
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN,
        description="Format for message content: text/md (default) or text/plain.",
    )
    limit: int = Field(default=50, ge=1, le=100, description="Messages per page (1–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor. Pass the `next_cursor` from a previous response to get the next page.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class SendChatMessageInput(_WorkspaceMixin):
    channel_id: str = Field(min_length=1, description="Id of the Chat channel to post the message to.")
    content: str = Field(
        min_length=1,
        max_length=MAX_CONTENT_CHARS,
        description="Message body (markdown unless content_format is plain).",
    )
    message_type: MessageType = Field(
        default=MessageType.MESSAGE,
        description="Message kind: 'message' (default) or 'post' (rich post — pair with post_data + a subtype id).",
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format of `content`: text/md (default) or text/plain."
    )
    assignee: str | None = Field(default=None, description="User id to assign the message to.")
    group_assignee: str | None = Field(default=None, description="User-group id to assign the message to.")
    followers: list[str] | None = Field(
        default=None, max_length=10, description="Up to 10 user ids to add as followers of the message."
    )
    post_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Post payload for message_type='post': {'subtype': {'id': '...'}} using an id "
            "from clickup_get_chat_subtypes (title is also allowed alongside subtype)."
        ),
    )


class UpdateChatMessageInput(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the message to update.")
    content: str | None = Field(
        default=None, max_length=MAX_CONTENT_CHARS, description="New message body (omit to leave unchanged)."
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format of `content`: text/md (default) or text/plain."
    )
    assignee: str | None = Field(default=None, description="New assignee user id (omit to leave unchanged).")
    group_assignee: str | None = Field(default=None, description="New group-assignee id (omit to leave unchanged).")
    resolved: bool | None = Field(
        default=None,
        description="Mark the message resolved/unresolved (sent as a top-level `resolved` field). Omit to leave unchanged.",
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> UpdateChatMessageInput:
        if self.content is None and self.assignee is None and self.group_assignee is None and self.resolved is None:
            raise ValueError("Provide at least one of content, assignee, group_assignee, or resolved to update.")
        return self


class DeleteChatMessageInput(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the message to delete.")


class SendChatReplyInput(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the parent message to reply to.")
    content: str = Field(
        min_length=1, max_length=MAX_CONTENT_CHARS, description="Reply body (markdown unless content_format is plain)."
    )
    message_type: MessageType = Field(
        default=MessageType.MESSAGE, description="Reply kind: 'message' (default) or 'post'."
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format of `content`: text/md (default) or text/plain."
    )
    assignee: str | None = Field(default=None, description="User id to assign the reply to.")
    group_assignee: str | None = Field(default=None, description="User-group id to assign the reply to.")
    followers: list[str] | None = Field(
        default=None, max_length=10, description="Up to 10 user ids to add as followers of the reply."
    )
    post_data: dict[str, Any] | None = Field(
        default=None, description="Post payload for message_type='post' (subtype id, etc.)."
    )


class GetChatMessageRepliesInput(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the message whose replies to list.")
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format for reply content: text/md (default) or text/plain."
    )
    limit: int = Field(default=50, ge=1, le=100, description="Replies per page (1–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None, description="Pagination cursor. Pass the `next_cursor` from a previous response."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class _ReactionMixin(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the message to react to.")
    reaction: str = Field(min_length=1, description="Lower-case emoji name (e.g. 'thumbsup', 'heart', 'tada').")

    @field_validator("reaction")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class AddChatReactionInput(_ReactionMixin):
    pass


class DeleteChatReactionInput(_ReactionMixin):
    pass


class GetChatMessageReactionsInput(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the message whose reactions to list.")
    limit: int = Field(default=50, ge=1, le=100, description="Reactions per page (1–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None, description="Pagination cursor. Pass the `next_cursor` from a previous response."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class GetChatMessageTaggedUsersInput(_WorkspaceMixin):
    message_id: str = Field(min_length=1, description="Id of the message whose @-tagged users to list.")
    limit: int = Field(default=50, ge=1, le=100, description="Users per page (1–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None, description="Pagination cursor. Pass the `next_cursor` from a previous response."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class GetChatSubtypesInput(_WorkspaceMixin):
    comment_type: CommentType = Field(
        default=CommentType.POST,
        description="Which subtype family to list: post (default), ai, syncup, or ai_via_brain.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


# ---------------------------------------------------------------------------
# Tools — read
# ---------------------------------------------------------------------------
@mcp.tool(
    name="clickup_get_chat_messages",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Messages",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_messages(params: GetChatMessagesInput) -> str:
    """List the messages in a Chat channel (newest first), cursor-paginated.

    When to Use:
    - To read a channel's conversation, or to grab a message id before replying,
      reacting, updating, or deleting.

    When NOT to Use:
    - To list channels themselves — use `clickup_get_chat_channels` (chat_channels).
    - To read one message's replies — use `clickup_get_chat_message_replies`.

    Returns:
    Each message's author, id, timestamp, reply count, and a content snippet. When
    more results exist the output includes a `next_cursor` to page forward.

    Pagination:
    Cursor-based. Call once, read `next_cursor`, then call again with
    `cursor=<next_cursor>` until it is empty.

    Examples:
    - params = {"channel_id": "6-901..."}
    - params = {"channel_id": "6-901...", "cursor": "eyJ...", "limit": 100}

    Error Handling:
    404 → unknown channel id; 401 → bad token. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"limit": params.limit, "content_format": params.content_format.value}
        if params.cursor:
            query["cursor"] = params.cursor
        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}/messages",
            params=query,
            use_v3=True,
        )
        messages, next_cursor = _extract_list(resp.json(), "messages")
        return _render_list(
            items=messages,
            next_cursor=next_cursor,
            fmt=params.response_format,
            json_key="messages",
            title=f"Messages in channel `{params.channel_id}`",
            item_formatter=_format_message,
            empty_note="_No messages._",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_message_replies",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Message Replies",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_message_replies(params: GetChatMessageRepliesInput) -> str:
    """List the replies threaded under a Chat message, cursor-paginated.

    When to Use:
    - To read a thread hanging off a specific message.

    When NOT to Use:
    - To list top-level channel messages — use `clickup_get_chat_messages`.

    Returns:
    Each reply's author, id, timestamp, and content snippet, plus a `next_cursor`
    when more replies exist.

    Pagination:
    Cursor-based (see `clickup_get_chat_messages`).

    Examples:
    - params = {"message_id": "abc123"}
    - params = {"message_id": "abc123", "cursor": "eyJ..."}

    Error Handling:
    404 → unknown message id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"limit": params.limit, "content_format": params.content_format.value}
        if params.cursor:
            query["cursor"] = params.cursor
        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}/replies",
            params=query,
            use_v3=True,
        )
        replies, next_cursor = _extract_list(resp.json(), "replies", "messages")
        return _render_list(
            items=replies,
            next_cursor=next_cursor,
            fmt=params.response_format,
            json_key="replies",
            title=f"Replies to message `{params.message_id}`",
            item_formatter=_format_message,
            empty_note="_No replies._",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_message_reactions",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Message Reactions",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_message_reactions(params: GetChatMessageReactionsInput) -> str:
    """List the emoji reactions on a Chat message, cursor-paginated.

    When to Use:
    - To see who reacted to a message and with which emoji.

    When NOT to Use:
    - To add or remove a reaction — use `clickup_add_chat_reaction` /
      `clickup_delete_chat_reaction`.

    Returns:
    Each reaction's emoji name and the user who added it, plus a `next_cursor`
    when more exist.

    Pagination:
    Cursor-based (see `clickup_get_chat_messages`).

    Examples:
    - params = {"message_id": "abc123"}

    Error Handling:
    404 → unknown message id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"limit": params.limit}
        if params.cursor:
            query["cursor"] = params.cursor
        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}/reactions",
            params=query,
            use_v3=True,
        )
        reactions, next_cursor = _extract_list(resp.json(), "reactions")
        return _render_list(
            items=reactions,
            next_cursor=next_cursor,
            fmt=params.response_format,
            json_key="reactions",
            title=f"Reactions on message `{params.message_id}`",
            item_formatter=_format_reaction,
            empty_note="_No reactions._",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_message_tagged_users",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Message Tagged Users",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_message_tagged_users(params: GetChatMessageTaggedUsersInput) -> str:
    """List the users @-tagged (mentioned) in a Chat message, cursor-paginated.

    When to Use:
    - To find who was mentioned in a message (e.g. to follow up or notify).

    When NOT to Use:
    - To list a channel's members — use `clickup_get_chat_channel_members`
      (chat_channels).

    Returns:
    Each tagged user's name and id, plus a `next_cursor` when more exist.

    Pagination:
    Cursor-based (see `clickup_get_chat_messages`).

    Examples:
    - params = {"message_id": "abc123"}

    Error Handling:
    404 → unknown message id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"limit": params.limit}
        if params.cursor:
            query["cursor"] = params.cursor
        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}/tagged_users",
            params=query,
            use_v3=True,
        )
        users, next_cursor = _extract_list(resp.json(), "tagged_users", "users")
        return _render_list(
            items=users,
            next_cursor=next_cursor,
            fmt=params.response_format,
            json_key="tagged_users",
            title=f"Users tagged in message `{params.message_id}`",
            item_formatter=_format_tagged_user,
            empty_note="_No tagged users._",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_subtypes",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Post Subtypes",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_subtypes(params: GetChatSubtypesInput) -> str:
    """List a Workspace's post subtype IDs (Announcement, Discussion, Idea, Update).

    Subtype IDs are unique per Workspace and are required when sending a rich
    'post' message: pass the chosen id via `post_data={"subtype": {"id": "..."}}`
    on `clickup_send_chat_message` (or `clickup_send_chat_reply`) with
    message_type='post'.

    When to Use:
    - Before sending a post-type chat message, to resolve the subtype id to use.

    When NOT to Use:
    - For plain messages — post subtypes only apply to message_type='post'.

    Returns:
    Each subtype's name and id for the requested comment family.

    Examples:
    - params = {"comment_type": "post"}

    Error Handling:
    404 → unknown Workspace. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/comments/types/{params.comment_type.value}/subtypes",
            use_v3=True,
        )
        subtypes, _ = _extract_list(resp.json(), "subtypes")
        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json({"count": len(subtypes), "subtypes": subtypes}))
        lines = [f"# Post subtypes ({params.comment_type.value}) — {len(subtypes)}", ""]
        for subtype in subtypes:
            lines.append(_format_subtype(subtype))
        if not subtypes:
            lines.append("_No subtypes._")
        return _cap("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Tools — write
# ---------------------------------------------------------------------------
@mcp.tool(
    name="clickup_send_chat_message",
    annotations=ToolAnnotations(
        title="Send ClickUp Chat Message",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_send_chat_message(params: SendChatMessageInput) -> str:
    """Post a new message to a Chat channel.

    Defaults to a plain 'message'. For a rich 'post' (Announcement/Discussion/Idea/
    Update), set message_type='post' and pass the subtype id in
    `post_data={"subtype": {"id": "..."}}` — get the id from `clickup_get_chat_subtypes`.

    When to Use:
    - To send a message into a channel.

    When NOT to Use:
    - To reply within a thread — use `clickup_send_chat_reply`.
    - To change an existing message — use `clickup_update_chat_message`.

    Returns:
    A confirmation with the new message's id and channel.

    Examples:
    - params = {"channel_id": "6-901...", "content": "Deploy is green ✅"}
    - params = {"channel_id": "6-901...", "content": "Q3 kickoff", "message_type": "post",
                "post_data": {"subtype": {"id": "123"}}}

    Error Handling:
    400 → bad body (e.g. content too long); 404 → unknown channel. Errors return an
    `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {
            "type": params.message_type.value,
            "content": params.content,
            "content_format": params.content_format.value,
        }
        if params.assignee is not None:
            body["assignee"] = params.assignee
        if params.group_assignee is not None:
            body["group_assignee"] = params.group_assignee
        if params.followers is not None:
            body["followers"] = params.followers
        if params.post_data is not None:
            body["post_data"] = params.post_data
        client = get_client()
        resp = await client.request(
            "POST",
            f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}/messages",
            json_body=body,
            use_v3=True,
        )
        message = _extract_object(resp.json(), "message")
        new_id = message.get("id", "?")
        return f"Sent {params.message_type.value} (id `{new_id}`) to channel `{params.channel_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_send_chat_reply",
    annotations=ToolAnnotations(
        title="Reply to ClickUp Chat Message",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_send_chat_reply(params: SendChatReplyInput) -> str:
    """Post a threaded reply under an existing Chat message.

    When to Use:
    - To respond within a message's thread.

    When NOT to Use:
    - To start a new top-level message — use `clickup_send_chat_message`.

    Returns:
    A confirmation with the new reply's id and the parent message id.

    Examples:
    - params = {"message_id": "abc123", "content": "On it 👍"}

    Error Handling:
    400 → bad body; 404 → unknown parent message. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {
            "type": params.message_type.value,
            "content": params.content,
            "content_format": params.content_format.value,
        }
        if params.assignee is not None:
            body["assignee"] = params.assignee
        if params.group_assignee is not None:
            body["group_assignee"] = params.group_assignee
        if params.followers is not None:
            body["followers"] = params.followers
        if params.post_data is not None:
            body["post_data"] = params.post_data
        client = get_client()
        resp = await client.request(
            "POST",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}/replies",
            json_body=body,
            use_v3=True,
        )
        reply = _extract_object(resp.json(), "message", "reply")
        new_id = reply.get("id", "?")
        return f"Replied (id `{new_id}`) to message `{params.message_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_chat_message",
    annotations=ToolAnnotations(
        title="Update ClickUp Chat Message",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_chat_message(params: UpdateChatMessageInput) -> str:
    """Edit a Chat message's content, assignee, or resolved state.

    Send only the fields you want to change; `resolved` is a top-level
    body field (not part of `post_data`).

    When to Use:
    - To fix a message's text, (re)assign it, or mark a post resolved/unresolved.

    When NOT to Use:
    - To remove a message entirely — use `clickup_delete_chat_message`.
    - To react to it — use `clickup_add_chat_reaction`.

    Returns:
    A confirmation naming the message and the fields changed.

    Examples:
    - params = {"message_id": "abc123", "content": "edited text"}
    - params = {"message_id": "abc123", "resolved": true}

    Error Handling:
    404 → unknown message id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {}
        changed: list[str] = []
        if params.content is not None:
            body["content"] = params.content
            body["content_format"] = params.content_format.value
            changed.append("content")
        if params.assignee is not None:
            body["assignee"] = params.assignee
            changed.append("assignee")
        if params.group_assignee is not None:
            body["group_assignee"] = params.group_assignee
            changed.append("group_assignee")
        if params.resolved is not None:
            body["resolved"] = params.resolved
            changed.append("resolved")
        client = get_client()
        await client.request(
            "PATCH",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}",
            json_body=body,
            use_v3=True,
        )
        return f"Updated message `{params.message_id}` ({', '.join(changed)})."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_chat_message",
    annotations=ToolAnnotations(
        title="Delete ClickUp Chat Message",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_chat_message(params: DeleteChatMessageInput) -> str:
    """Permanently delete a Chat message (and its replies).

    When to Use:
    - To remove a message you posted in error.

    When NOT to Use:
    - To edit it instead — use `clickup_update_chat_message`.

    Returns:
    A confirmation that the message was deleted.

    Examples:
    - params = {"message_id": "abc123"}

    Error Handling:
    404 → already gone / unknown id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        await client.request(
            "DELETE",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}",
            use_v3=True,
        )
        return f"Deleted message `{params.message_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_add_chat_reaction",
    annotations=ToolAnnotations(
        title="Add ClickUp Chat Reaction",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_add_chat_reaction(params: AddChatReactionInput) -> str:
    """Add an emoji reaction to a Chat message.

    `reaction` is the lower-case emoji name (e.g. `thumbsup`, `heart`, `tada`);
    it is lower-cased for you.

    When to Use:
    - To react to a message with an emoji.

    When NOT to Use:
    - To remove a reaction — use `clickup_delete_chat_reaction`.
    - To list reactions — use `clickup_get_chat_message_reactions`.

    Returns:
    A confirmation naming the emoji and message.

    Examples:
    - params = {"message_id": "abc123", "reaction": "thumbsup"}

    Error Handling:
    400 → unknown emoji name; 404 → unknown message. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        await client.request(
            "POST",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}/reactions",
            json_body={"reaction": params.reaction},
            use_v3=True,
        )
        return f"Added reaction `{params.reaction}` to message `{params.message_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_chat_reaction",
    annotations=ToolAnnotations(
        title="Delete ClickUp Chat Reaction",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_chat_reaction(params: DeleteChatReactionInput) -> str:
    """Remove an emoji reaction from a Chat message.

    `reaction` is the lower-case emoji name and is sent as a path segment.

    When to Use:
    - To undo a reaction you added.

    When NOT to Use:
    - To add one — use `clickup_add_chat_reaction`.

    Returns:
    A confirmation that the reaction was removed.

    Examples:
    - params = {"message_id": "abc123", "reaction": "thumbsup"}

    Error Handling:
    404 → reaction/message not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        encoded_reaction = urllib.parse.quote(params.reaction, safe="")
        await client.request(
            "DELETE",
            f"/workspaces/{workspace_id}/chat/messages/{params.message_id}/reactions/{encoded_reaction}",
            use_v3=True,
        )
        return f"Removed reaction `{params.reaction}` from message `{params.message_id}`."
    except Exception as exc:
        return handle_api_error(exc)
