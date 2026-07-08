"""ClickUp Chat channel tools (API v3).

Chat lives entirely on the ClickUp v3 surface
(`/api/v3/workspaces/{workspace_id}/chat/channels...`), so every request here goes
through `client.request(..., use_v3=True)`.

Three ways to create a channel:
- `clickup_create_chat_channel` — a plain Workspace-level Channel by name (returns
  the existing Channel if one with that name already exists).
- `clickup_create_location_chat_channel` — a Channel bound to a Space, Folder, or
  List (the location object carries `{id, type}`; there is no `name` field — the
  name is derived from the location).
- `clickup_create_direct_message` — a 1:1 or group DM from up to 15 user ids
  (empty user_ids creates a Self DM).

Pagination is cursor-based and **asymmetric**: send the `cursor` query param, read
the `next_cursor` field from the response, and pass it back as `cursor` to page
forward. Description/content format values are `text/md` (default) or `text/plain`.

Sibling module `tools/chat_messages.py` (t17) owns messages, replies, and
reactions — this module stops at the channel, its followers, and its members.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Display / byte guards independent of API paging, to stay under the 1 MB MCP limit.
MAX_DISPLAY_ITEMS = 50
MAX_OUTPUT_CHARS = 800_000

_MAX_CHANNEL_MEMBERS = 100
_MAX_DM_MEMBERS = 15


class ContentFormat(StrEnum):
    """Format for a channel's text fields on the wire."""

    MARKDOWN = "text/md"
    PLAIN = "text/plain"


class Visibility(StrEnum):
    """Channel visibility."""

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class ChannelType(StrEnum):
    """Channel kinds returned/filtered by the API."""

    CHANNEL = "CHANNEL"
    DM = "DM"
    GROUP_DM = "GROUP_DM"


class LocationType(StrEnum):
    """Hierarchy level a location-bound channel attaches to."""

    SPACE = "space"
    FOLDER = "folder"
    LIST = "list"


def _resolve_workspace_id(workspace_id: str | None) -> str:
    """Return the caller's workspace id, else the configured default team id."""
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


# ---------------------------------------------------------------------------
# Response-shape helpers (ClickUp v3 is loose about bare-array vs keyed-object)
# ---------------------------------------------------------------------------
def _extract_items(payload: Any, *keys: str) -> tuple[list[dict[str, Any]], str | None]:
    """Normalize a v3 list response to (items, next_cursor).

    Accepts a bare array or an object keyed by any of `keys` (falling back to the
    common `data`/`items` wrappers).
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], None
    if isinstance(payload, dict):
        for key in (*keys, "data", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                items = [x for x in val if isinstance(x, dict)]
                cursor = payload.get("next_cursor") or payload.get("cursor")
                return items, cursor if isinstance(cursor, str) and cursor else None
    return [], None


def _unwrap_channel(payload: Any) -> dict[str, Any]:
    """Pull the channel object out of a create/get response (wrapped or bare)."""
    if isinstance(payload, dict):
        for key in ("channel", "data"):
            inner = payload.get(key)
            if isinstance(inner, dict):
                return inner
        return payload
    return {}


def _format_channel(ch: dict[str, Any]) -> str:
    name = ch.get("name") or "(unnamed)"
    parts = [f"- **{name}** (id `{ch.get('id', '?')}`)"]
    if ch.get("type"):
        parts.append(f" — {ch.get('type')}")
    if ch.get("visibility"):
        parts.append(f", {ch.get('visibility')}")
    if ch.get("archived"):
        parts.append(", archived")
    return "".join(parts)


def _format_channel_detail(ch: dict[str, Any]) -> str:
    lines = [f"# {ch.get('name') or '(unnamed channel)'}", "", f"- id: `{ch.get('id', '?')}`"]
    for key in ("type", "visibility", "topic", "description"):
        val = ch.get(key)
        if val not in (None, ""):
            lines.append(f"- {key}: {val}")
    if ch.get("archived") is not None:
        lines.append(f"- archived: {ch.get('archived')}")
    parent = ch.get("parent")
    if isinstance(parent, dict) and parent.get("id") is not None:
        lines.append(f"- parent: type `{parent.get('type')}` id `{parent.get('id')}`")
    if ch.get("date_created"):
        lines.append(f"- created: {epoch_to_human(ch.get('date_created'))}")
    for key in ("created_at", "updated_at"):
        if ch.get(key):
            lines.append(f"- {key}: {ch.get(key)}")
    return "\n".join(lines)


def _format_member(m: dict[str, Any]) -> str:
    raw = m.get("user")
    user: dict[str, Any] = raw if isinstance(raw, dict) else m
    name = user.get("username") or user.get("name") or user.get("email") or "(unknown)"
    email = user.get("email")
    suffix = f" — {email}" if email else ""
    return f"- **{name}** (id `{user.get('id', '?')}`){suffix}"


def _render_list(
    *,
    items: list[dict[str, Any]],
    next_cursor: str | None,
    fmt: ResponseFormat,
    title: str,
    item_formatter: Any,
    empty: str,
) -> str:
    """Uniform cursor-paginated output shared by the three list tools."""
    if fmt is ResponseFormat.JSON:
        return _cap(to_json({"count": len(items), "next_cursor": next_cursor, "items": items}))

    shown = items[:MAX_DISPLAY_ITEMS]
    lines = [f"# {title} ({len(items)} found)", ""]
    if len(items) > len(shown):
        lines.append(f"_Showing first {len(shown)}._")
    for item in shown:
        lines.append(item_formatter(item))
    if not items:
        lines.append(empty)
    if next_cursor:
        lines.append("")
        lines.append(f"More available — call again with `cursor` = `{next_cursor}`.")
    return _cap("\n".join(lines))


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class GetChannelsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    description_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN,
        description="Format for channel descriptions: text/md (default) or text/plain.",
    )
    is_follower: bool = Field(default=False, description="Return only channels the authorized user follows.")
    include_closed: bool = Field(default=False, description="Include explicitly closed DMs / Group DMs.")
    channel_types: list[ChannelType] | None = Field(
        default=None,
        description="Filter by channel kind(s): CHANNEL, DM, and/or GROUP_DM. Omit for all kinds.",
    )
    with_message_since: int | None = Field(
        default=None,
        ge=0,
        description="Only channels with a message after this unix timestamp in milliseconds.",
    )
    limit: int = Field(default=50, ge=1, le=100, description="Channels per page (1–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor. Pass the `next_cursor` from a previous response to get the next page.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class CreateChannelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, description="Name of the Channel. Returns the existing Channel if it matches.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    description: str | None = Field(default=None, description="Channel description.")
    topic: str | None = Field(default=None, description="Channel topic (short purpose line).")
    user_ids: list[str] | None = Field(
        default=None,
        max_length=_MAX_CHANNEL_MEMBERS,
        description="User ids to add as members (up to 100).",
    )
    visibility: Visibility | None = Field(
        default=None, description="PUBLIC (default) or PRIVATE. Omit to use the ClickUp default (PUBLIC)."
    )


class CreateLocationChannelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    location_id: str = Field(min_length=1, description="Id of the Space, Folder, or List to bind the Channel to.")
    location_type: LocationType = Field(description="Hierarchy level of location_id: space, folder, or list.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    description: str | None = Field(default=None, description="Channel description.")
    topic: str | None = Field(default=None, description="Channel topic (short purpose line).")
    user_ids: list[str] | None = Field(
        default=None,
        max_length=_MAX_CHANNEL_MEMBERS,
        description="User ids to add as members (up to 100).",
    )
    visibility: Visibility | None = Field(
        default=None, description="PUBLIC (default) or PRIVATE. Omit to use the ClickUp default (PUBLIC)."
    )


class CreateDirectMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    user_ids: list[str] | None = Field(
        default=None,
        max_length=_MAX_DM_MEMBERS,
        description="Participant user ids (up to 15). Omit or leave empty to create a Self DM.",
    )


class GetChannelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel_id: str = Field(min_length=1, description="Id of the Channel to fetch.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    description_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN,
        description="Format for the channel description: text/md (default) or text/plain.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class UpdateChannelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel_id: str = Field(min_length=1, description="Id of the Channel to update.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    name: str | None = Field(default=None, description="New channel name (omit to leave unchanged).")
    description: str | None = Field(default=None, description="New channel description (omit to leave unchanged).")
    topic: str | None = Field(default=None, description="New channel topic (omit to leave unchanged).")
    visibility: Visibility | None = Field(default=None, description="New visibility: PUBLIC or PRIVATE.")
    location_id: str | None = Field(
        default=None, description="Move the Channel to this Space/Folder/List id (requires location_type)."
    )
    location_type: LocationType | None = Field(
        default=None, description="Hierarchy level of location_id: space, folder, or list (requires location_id)."
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN,
        description="Format of the description/topic being sent: text/md (default) or text/plain.",
    )

    @model_validator(mode="after")
    def _validate(self) -> UpdateChannelInput:
        if (self.location_id is None) != (self.location_type is None):
            raise ValueError("location_id and location_type must be provided together (or both omitted).")
        if (
            self.name is None
            and self.description is None
            and self.topic is None
            and self.visibility is None
            and self.location_id is None
        ):
            raise ValueError("Provide at least one of name, description, topic, visibility, or location to update.")
        return self


class DeleteChannelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel_id: str = Field(min_length=1, description="Id of the Channel to delete.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )


class ChannelMembersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel_id: str = Field(min_length=1, description="Id of the Channel whose members/followers to list.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    limit: int = Field(default=50, ge=1, le=100, description="Results per page (1–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor. Pass the `next_cursor` from a previous response to get the next page.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(
    name="clickup_get_chat_channels",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Channels",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_channels(params: GetChannelsInput) -> str:
    """List the Chat channels in a Workspace, with descriptor filters.

    Returns Channels, DMs, and Group DMs the authorized user can see. Narrow the
    result with `channel_types`, `is_follower`, `include_closed`, or
    `with_message_since`. Results are cursor-paginated.

    When to Use:
    - To find a channel's id before reading it (`clickup_get_chat_channel`),
      posting to it, or listing its members.
    - To enumerate only DMs (`channel_types=["DM","GROUP_DM"]`) or only followed
      channels (`is_follower=true`).

    When NOT to Use:
    - To read messages in a channel — that lives in `clickup_get_chat_messages`
      (chat_messages module).

    Returns:
    A list of channels (name, id, type, visibility). When more results exist the
    response includes a `next_cursor`; pass it back as `cursor` to page forward.

    Pagination:
    Cursor-based and asymmetric. Loop: call once, read `next_cursor` from the
    output, then call again with `cursor=<next_cursor>` until it is empty.

    Examples:
    - params = {"channel_types": ["CHANNEL"], "limit": 100}
    - params = {"is_follower": true, "cursor": "eyJ..."}

    Error Handling:
    401 → bad token; 404 → unknown Workspace id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {
            "description_format": params.description_format.value,
            "is_follower": params.is_follower,
            "include_closed": params.include_closed,
            "limit": params.limit,
        }
        if params.channel_types:
            query["channel_types"] = [t.value for t in params.channel_types]
        if params.with_message_since is not None:
            query["with_message_since"] = params.with_message_since
        if params.cursor:
            query["cursor"] = params.cursor

        client = get_client()
        resp = await client.request("GET", f"/workspaces/{workspace_id}/chat/channels", params=query, use_v3=True)
        channels, next_cursor = _extract_items(resp.json(), "channels")
        return _render_list(
            items=channels,
            next_cursor=next_cursor,
            fmt=params.response_format,
            title="Chat channels",
            item_formatter=_format_channel,
            empty="_No channels matched._",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_chat_channel",
    annotations=ToolAnnotations(
        title="Create ClickUp Chat Channel",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_chat_channel(params: CreateChannelInput) -> str:
    """Create a Workspace-level Chat channel by name.

    If a Channel with the given name already exists, ClickUp returns that
    existing Channel instead of creating a duplicate.

    When to Use:
    - To open a general, non-location-bound channel (e.g. "#announcements").

    When NOT to Use:
    - To attach a channel to a Space/Folder/List — use
      `clickup_create_location_chat_channel`.
    - To start a direct message — use `clickup_create_direct_message`.

    Returns:
    A confirmation with the channel's name, id, and visibility.

    Examples:
    - params = {"name": "announcements", "visibility": "PUBLIC"}
    - params = {"name": "team-x", "user_ids": ["123", "456"], "topic": "Team X"}

    Error Handling:
    400 → bad payload; 401 → bad token. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {"name": params.name}
        if params.description is not None:
            body["description"] = params.description
        if params.topic is not None:
            body["topic"] = params.topic
        if params.user_ids is not None:
            body["user_ids"] = params.user_ids
        if params.visibility is not None:
            body["visibility"] = params.visibility.value

        client = get_client()
        resp = await client.request("POST", f"/workspaces/{workspace_id}/chat/channels", json_body=body, use_v3=True)
        channel = _unwrap_channel(resp.json())
        vis = channel.get("visibility") or (params.visibility.value if params.visibility else "PUBLIC")
        return f"Created chat channel **{channel.get('name') or params.name}** (id `{channel.get('id', '?')}`, {vis})."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_location_chat_channel",
    annotations=ToolAnnotations(
        title="Create ClickUp Location Chat Channel",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_location_chat_channel(params: CreateLocationChannelInput) -> str:
    """Create a Chat channel bound to a Space, Folder, or List.

    The channel's name is derived from the location, so there is no `name`
    field — pass the location id and its type instead. The location is sent as
    `{"id": <location_id>, "type": <space|folder|list>}`.

    When to Use:
    - To give a specific Space/Folder/List its own conversation channel.

    When NOT to Use:
    - For a standalone, named channel — use `clickup_create_chat_channel`.
    - For a DM — use `clickup_create_direct_message`.

    Returns:
    A confirmation with the channel's name, id, and bound location.

    Examples:
    - params = {"location_id": "901300", "location_type": "space"}
    - params = {"location_id": "L9", "location_type": "list", "visibility": "PRIVATE"}

    Error Handling:
    400 → bad location; 404 → location not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {"location": {"id": params.location_id, "type": params.location_type.value}}
        if params.description is not None:
            body["description"] = params.description
        if params.topic is not None:
            body["topic"] = params.topic
        if params.user_ids is not None:
            body["user_ids"] = params.user_ids
        if params.visibility is not None:
            body["visibility"] = params.visibility.value

        client = get_client()
        resp = await client.request(
            "POST", f"/workspaces/{workspace_id}/chat/channels/location", json_body=body, use_v3=True
        )
        channel = _unwrap_channel(resp.json())
        name = channel.get("name") or "(unnamed)"
        return (
            f"Created chat channel **{name}** (id `{channel.get('id', '?')}`) "
            f"in {params.location_type.value} `{params.location_id}`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_direct_message",
    annotations=ToolAnnotations(
        title="Create ClickUp Direct Message Channel",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_direct_message(params: CreateDirectMessageInput) -> str:
    """Create (or return) a direct-message Chat channel with up to 15 users.

    One recipient id makes a 1:1 DM; two or more make a Group DM. Omit `user_ids`
    (or pass an empty list) to create a Self DM. ClickUp returns the existing DM
    channel if one already exists for the same participant set.

    When to Use:
    - To message one or a few users directly rather than in a shared channel.

    When NOT to Use:
    - For a named or location-bound channel — use `clickup_create_chat_channel`
      or `clickup_create_location_chat_channel`.

    Returns:
    A confirmation with the DM channel's id and participant count.

    Examples:
    - params = {"user_ids": ["123"]}
    - params = {"user_ids": ["123", "456", "789"]}
    - params = {}   # Self DM

    Error Handling:
    400 → more than 15 users / bad id; 401 → bad token. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        user_ids = params.user_ids or []
        body: dict[str, Any] = {"user_ids": user_ids}

        client = get_client()
        resp = await client.request(
            "POST", f"/workspaces/{workspace_id}/chat/channels/direct_message", json_body=body, use_v3=True
        )
        channel = _unwrap_channel(resp.json())
        kind = "Self DM" if not user_ids else f"DM with {len(user_ids)} participant(s)"
        return f"Created direct message channel (id `{channel.get('id', '?')}`) — {kind}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_channel",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Channel",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_channel(params: GetChannelInput) -> str:
    """Fetch a single Chat channel's metadata by id.

    When to Use:
    - To confirm a channel's type, visibility, topic, or bound location by id.

    When NOT to Use:
    - To list many channels — use `clickup_get_chat_channels`.
    - To read its messages — use `clickup_get_chat_messages`.

    Returns:
    The channel's name, id, type, visibility, topic, description, and location.

    Examples:
    - params = {"channel_id": "6-901300-8"}

    Error Handling:
    404 → channel not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query = {"description_format": params.description_format.value}
        client = get_client()
        resp = await client.request(
            "GET", f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}", params=query, use_v3=True
        )
        channel = _unwrap_channel(resp.json())
        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json(channel))
        return _cap(_format_channel_detail(channel))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_chat_channel",
    annotations=ToolAnnotations(
        title="Update ClickUp Chat Channel",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_chat_channel(params: UpdateChannelInput) -> str:
    """Update a Chat channel's name, description, topic, visibility, or location.

    Send only the fields you want to change (at least one is required). Text is
    interpreted per `content_format` — `text/md` (default) or `text/plain`. To
    re-bind the channel to another Space/Folder/List, pass `location_id` and
    `location_type` together.

    When to Use:
    - To rename a channel, update its topic/description, or toggle visibility.

    When NOT to Use:
    - To delete a channel — use `clickup_delete_chat_channel`.

    Returns:
    A confirmation listing the fields that were updated.

    Examples:
    - params = {"channel_id": "6-901300-8", "topic": "Sprint 42"}
    - params = {"channel_id": "6-901300-8", "visibility": "PRIVATE"}

    Error Handling:
    400 → bad payload; 404 → channel not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {"content_format": params.content_format.value}
        changed: list[str] = []
        if params.name is not None:
            body["name"] = params.name
            changed.append("name")
        if params.description is not None:
            body["description"] = params.description
            changed.append("description")
        if params.topic is not None:
            body["topic"] = params.topic
            changed.append("topic")
        if params.visibility is not None:
            body["visibility"] = params.visibility.value
            changed.append("visibility")
        if params.location_id is not None and params.location_type is not None:
            body["location"] = {"id": params.location_id, "type": params.location_type.value}
            changed.append("location")

        client = get_client()
        resp = await client.request(
            "PATCH", f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}", json_body=body, use_v3=True
        )
        channel = _unwrap_channel(resp.json())
        name = channel.get("name")
        label = f" **{name}**" if name else ""
        return f"Updated chat channel{label} (id `{params.channel_id}`) — changed: {', '.join(changed)}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_chat_channel",
    annotations=ToolAnnotations(
        title="Delete ClickUp Chat Channel",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_delete_chat_channel(params: DeleteChannelInput) -> str:
    """Permanently delete a Chat channel (Channel, DM, or location-bound).

    When to Use:
    - To remove a channel that is no longer needed.

    When NOT to Use:
    - To only hide/archive it — update its state via `clickup_update_chat_channel`.

    Returns:
    A confirmation that the channel was deleted.

    Examples:
    - params = {"channel_id": "6-901300-8"}

    Error Handling:
    404 → channel not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        await client.request("DELETE", f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}", use_v3=True)
        return f"Deleted chat channel `{params.channel_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_channel_followers",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Channel Followers",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_channel_followers(params: ChannelMembersInput) -> str:
    """List the users following a Chat channel.

    Followers receive notifications for the channel but are not necessarily
    members. Results are cursor-paginated.

    When to Use:
    - To see who is subscribed to a channel's activity.

    When NOT to Use:
    - To see who can access/post — use `clickup_get_chat_channel_members`.

    Returns:
    A list of follower users (name, id, email). When more results exist the
    response includes a `next_cursor`; pass it back as `cursor` to page forward.

    Pagination:
    Cursor-based. Loop with `cursor=<next_cursor>` until it is empty.

    Examples:
    - params = {"channel_id": "6-901300-8"}
    - params = {"channel_id": "6-901300-8", "cursor": "eyJ...", "limit": 100}

    Error Handling:
    404 → channel not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"limit": params.limit}
        if params.cursor:
            query["cursor"] = params.cursor

        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}/followers",
            params=query,
            use_v3=True,
        )
        followers, next_cursor = _extract_items(resp.json(), "followers", "members")
        return _render_list(
            items=followers,
            next_cursor=next_cursor,
            fmt=params.response_format,
            title="Channel followers",
            item_formatter=_format_member,
            empty="_No followers._",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_chat_channel_members",
    annotations=ToolAnnotations(
        title="Get ClickUp Chat Channel Members",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_chat_channel_members(params: ChannelMembersInput) -> str:
    """List the users who are members of a Chat channel.

    Members can access and post to the channel. Results are cursor-paginated.

    When to Use:
    - To audit who has access to a private channel or DM.

    When NOT to Use:
    - To see subscribers only — use `clickup_get_chat_channel_followers`.

    Returns:
    A list of member users (name, id, email). When more results exist the
    response includes a `next_cursor`; pass it back as `cursor` to page forward.

    Pagination:
    Cursor-based. Loop with `cursor=<next_cursor>` until it is empty.

    Examples:
    - params = {"channel_id": "6-901300-8"}
    - params = {"channel_id": "6-901300-8", "cursor": "eyJ...", "limit": 100}

    Error Handling:
    404 → channel not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"limit": params.limit}
        if params.cursor:
            query["cursor"] = params.cursor

        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/chat/channels/{params.channel_id}/members",
            params=query,
            use_v3=True,
        )
        members, next_cursor = _extract_items(resp.json(), "members")
        return _render_list(
            items=members,
            next_cursor=next_cursor,
            fmt=params.response_format,
            title="Channel members",
            item_formatter=_format_member,
            empty="_No members._",
        )
    except Exception as exc:
        return handle_api_error(exc)
