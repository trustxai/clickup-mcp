"""Lists tool module — Lists CRUD, folderless Lists, task membership, and templates.

Wave 1 (t3-lists). Covers ClickUp's List resource: creating Lists inside a
Folder or directly inside a Space ("folderless"), reading/updating/deleting a
List, adding or removing a Task's membership in an additional List (the
"Tasks in Multiple Lists" ClickApp), and instantiating Lists from templates.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, paginated_response, to_json
from clickup_mcp.server import mcp

# --------------------------------------------------------------------------
# Shared response-shape helpers (defensive against ClickUp's polymorphism)
# --------------------------------------------------------------------------


def _extract_list_obj(data: Any) -> dict[str, Any]:
    """Unwrap a `{"list": {...}}` envelope if present; else return `data` as-is.

    ClickUp's create/get endpoints normally return the List object directly,
    but some responses nest it under a `list` key — handle both shapes.
    """
    if isinstance(data, dict):
        nested = data.get("list")
        if isinstance(nested, dict) and "id" in nested:
            return nested
        return data
    return {}


def _extract_items(data: Any, key: str) -> list[dict[str, Any]]:
    """Handle bare-array vs. object-keyed-by-`key` polymorphism defensively."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _extract_color_status(value: Any) -> str | None:
    """List `status` represents the List *color*, returned as a bare string or
    `{"status": ..., "color": ..., "hide_label": ...}` depending on the endpoint."""
    if isinstance(value, dict):
        status = value.get("status")
        return str(status) if status is not None else None
    if isinstance(value, str) and value:
        return value
    return None


def _extract_priority_label(value: Any) -> str | None:
    if isinstance(value, dict):
        priority = value.get("priority")
        return str(priority) if priority is not None else None
    if value is None:
        return None
    return str(value)


def _extract_assignee_label(value: Any) -> str | None:
    if isinstance(value, dict):
        label = value.get("username") or value.get("email")
        if label:
            return str(label)
        user_id = value.get("id")
        return str(user_id) if user_id is not None else None
    return None


def _resolve_team_id(explicit: str) -> str:
    """Fall back to CLICKUP_TEAM_ID when the caller omits `team_id`."""
    team_id = explicit or get_settings().clickup_team_id
    if not team_id:
        raise RuntimeError(
            "No team_id provided and CLICKUP_TEAM_ID is not configured. Pass team_id explicitly or set "
            "CLICKUP_TEAM_ID in the environment/.env."
        )
    return team_id


def _format_list_item(item: dict[str, Any]) -> str:
    name = item.get("name", "Unnamed List")
    list_id = item.get("id", "unknown")
    folder = item.get("folder") or {}
    space = item.get("space") or {}
    task_count = item.get("task_count")
    due_date = epoch_to_human(item.get("due_date"))
    color_status = _extract_color_status(item.get("status"))
    priority = _extract_priority_label(item.get("priority"))
    archived = item.get("archived")

    details: list[str] = []
    if folder.get("name"):
        details.append(f"folder: {folder['name']}")
    if space.get("name"):
        details.append(f"space: {space['name']}")
    if task_count is not None:
        details.append(f"tasks: {task_count}")
    if due_date != "N/A":
        details.append(f"due: {due_date}")
    if color_status:
        details.append(f"color: {color_status}")
    if priority:
        details.append(f"priority: {priority}")
    if archived:
        details.append("archived")

    line = f"- **{name}** (id `{list_id}`)"
    if details:
        line += f"\n  - {', '.join(details)}"
    return line


def _format_list_detail(data: dict[str, Any], fmt: ResponseFormat) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json(data)

    name = data.get("name", "Unnamed List")
    list_id = data.get("id", "unknown")
    content = data.get("content") or "_No description._"
    folder = data.get("folder") or {}
    space = data.get("space") or {}
    due_date = epoch_to_human(data.get("due_date"))
    start_date = epoch_to_human(data.get("start_date"))
    color_status = _extract_color_status(data.get("status"))
    priority = _extract_priority_label(data.get("priority"))
    assignee = _extract_assignee_label(data.get("assignee"))
    archived = bool(data.get("archived"))
    task_count = data.get("task_count")

    folder_line = (
        f"- folder: {folder.get('name', 'N/A')} (id `{folder.get('id', 'N/A')}`)"
        if folder
        else "- folder: _none (folderless List)_"
    )

    lines = [
        f"# {name}",
        "",
        f"- id: `{list_id}`",
        folder_line,
        f"- space: {space.get('name', 'N/A')} (id `{space.get('id', 'N/A')}`)",
        f"- task count: {task_count if task_count is not None else 'N/A'}",
        f"- due date: {due_date}",
        f"- start date: {start_date}",
        f"- color: {color_status or 'N/A'}",
        f"- priority: {priority or 'N/A'}",
        f"- assignee: {assignee or 'N/A'}",
        f"- archived: {archived}",
        "",
        "## Description",
        content,
    ]
    return "\n".join(lines)


def _format_template_item(item: dict[str, Any]) -> str:
    name = item.get("name", "Unnamed Template")
    template_id = item.get("id", "unknown")
    return f"- **{name}** (id `{template_id}`)"


# --------------------------------------------------------------------------
# Input models
# --------------------------------------------------------------------------


class _ListMutationFields(BaseModel):
    """Optional List attributes shared by create and update request bodies."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content: str | None = Field(default=None, description="Plain-text List description.")
    markdown_content: str | None = Field(
        default=None,
        description="Markdown-formatted List description; use instead of `content` to format the description.",
    )
    due_date: int | None = Field(default=None, description="List due date as Unix epoch milliseconds.")
    due_date_time: bool | None = Field(
        default=None, description="Whether `due_date` carries a specific time of day (vs. date only)."
    )
    priority: int | None = Field(
        default=None,
        ge=1,
        le=4,
        description="List priority: 1=Urgent, 2=High, 3=Normal, 4=Low.",
    )
    assignee: int | None = Field(default=None, description="User id to set as the List's default assignee/owner.")
    status: str | None = Field(
        default=None,
        description=(
            "List color designation (e.g. 'blue') — this is the List's *color*, NOT a task status. Lists do not "
            "have their own task-style statuses; those come from the parent Folder/Space."
        ),
    )

    def to_body(self) -> dict[str, Any]:
        """Serialize only explicitly-set fields (path-only fields are excluded via `Field(exclude=True)`)."""
        return self.model_dump(exclude_none=True)


class CreateListInput(_ListMutationFields):
    """Body for creating a List inside a Folder."""

    folder_id: str = Field(..., exclude=True, description="Folder id to create the List in.")
    name: str = Field(..., min_length=1, description="Name of the new List.")


class CreateFolderlessListInput(_ListMutationFields):
    """Body for creating a List directly inside a Space (no Folder)."""

    space_id: str = Field(..., exclude=True, description="Space id to create the List in.")
    name: str = Field(..., min_length=1, description="Name of the new List.")


class GetListsInput(BaseModel):
    """Query for Lists that belong to a Folder."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="Folder id to list Lists from.")
    archived: bool = Field(default=False, description="Return archived Lists instead of active ones.")
    limit: int = Field(default=20, ge=1, le=100, description="Max Lists per page (display-side windowing).")
    offset: int = Field(default=0, ge=0, description="Number of Lists to skip (display-side windowing).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class GetFolderlessListsInput(BaseModel):
    """Query for Lists that live directly inside a Space (no Folder)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., description="Space id to list folderless Lists from.")
    archived: bool = Field(default=False, description="Return archived Lists instead of active ones.")
    limit: int = Field(default=20, ge=1, le=100, description="Max Lists per page (display-side windowing).")
    offset: int = Field(default=0, ge=0, description="Number of Lists to skip (display-side windowing).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class GetListInput(BaseModel):
    """Fetch a single List by id."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., description="List id to fetch.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class UpdateListInput(_ListMutationFields):
    """Body for updating a List. All fields are optional except `list_id`."""

    list_id: str = Field(..., exclude=True, description="List id to update.")
    name: str | None = Field(default=None, min_length=1, description="New name for the List.")
    unset_status: bool | None = Field(
        default=None,
        description="Set true to remove the List's color (`status`) instead of setting a new one.",
    )


class DeleteListInput(BaseModel):
    """Permanently delete a List."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., description="List id to permanently delete.")


class AddTaskToListInput(BaseModel):
    """Add an existing Task's membership to an additional List."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., description="List id to add the Task to.")
    task_id: str = Field(..., description="Task id to add.")


class RemoveTaskFromListInput(BaseModel):
    """Remove a Task's membership from an additional (non-home) List."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., description="List id to remove the Task from.")
    task_id: str = Field(..., description="Task id to remove.")


class CreateListFromTemplateInFolderInput(BaseModel):
    """Instantiate a List template inside a Folder."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="Folder id to create the new List in.")
    template_id: str = Field(..., description="List template id, `t-` prefixed (see clickup_get_list_templates).")
    name: str = Field(..., min_length=1, description="Name for the new List.")
    return_immediately: bool = Field(
        default=True,
        description=(
            "If true (default), ClickUp returns the future List id right away instead of waiting for every "
            "sub-object (tasks, custom fields, views, ...) to finish being created; small templates usually "
            "finish before the response is even sent."
        ),
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Extra template-creation options merged in alongside return_immediately (e.g. content, due_date, "
            "start_date, include_views, comment_attachments, old_due_date, old_start_date, automation, "
            "time_estimate) — see ClickUp's Create List from Template docs for the full set."
        ),
    )

    def to_body(self) -> dict[str, Any]:
        options: dict[str, Any] = {"return_immediately": self.return_immediately}
        if self.options:
            options.update(self.options)
        return {"name": self.name, "options": options}


class CreateListFromTemplateInSpaceInput(BaseModel):
    """Instantiate a List template directly inside a Space (no Folder)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., description="Space id to create the new List in.")
    template_id: str = Field(..., description="List template id, `t-` prefixed (see clickup_get_list_templates).")
    name: str = Field(..., min_length=1, description="Name for the new List.")
    return_immediately: bool = Field(
        default=True,
        description=(
            "If true (default), ClickUp returns the future List id right away instead of waiting for every "
            "sub-object (tasks, custom fields, views, ...) to finish being created; small templates usually "
            "finish before the response is even sent."
        ),
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Extra template-creation options merged in alongside return_immediately (e.g. content, due_date, "
            "start_date, include_views, comment_attachments, old_due_date, old_start_date, automation, "
            "time_estimate) — see ClickUp's Create List from Template docs for the full set."
        ),
    )

    def to_body(self) -> dict[str, Any]:
        options: dict[str, Any] = {"return_immediately": self.return_immediately}
        if self.options:
            options.update(self.options)
        return {"name": self.name, "options": options}


class GetListTemplatesInput(BaseModel):
    """List available List templates for a Workspace."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(
        default="", description="Workspace (team) id; falls back to CLICKUP_TEAM_ID env var if omitted."
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max templates per page (display-side windowing).")
    offset: int = Field(default=0, ge=0, description="Number of templates to skip (display-side windowing).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool(
    name="clickup_create_list",
    annotations=ToolAnnotations(
        title="Create List in Folder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_list(params: CreateListInput) -> str:
    """Create a new List inside a Folder.

    Lists inside a Folder normally inherit the Folder's statuses unless the
    Folder itself overrides Space statuses. Use
    `clickup_create_folderless_list` instead when the target Space is not
    organized into Folders.

    When to Use:
    - Setting up a new List for a team/project that already has a Folder.
    - Programmatically scaffolding a Workspace structure Folder-by-Folder.

    When NOT to Use:
    - The target Space has no Folders — use `clickup_create_folderless_list`.
    - Copying an established List's tasks/views/custom fields — use
      `clickup_create_list_from_template_in_folder` instead.

    Returns:
    A confirmation string with the new List's name and id, or an
    `Error ...` string.

    Examples:
    params = {"folder_id": "12345", "name": "Sprint 24", "priority": 2}
    params = {"folder_id": "12345", "name": "Backlog", "markdown_content": "**Unscheduled** work"}

    Error Handling:
    404 means folder_id does not exist or is not accessible; 400 usually
    means `name` is missing or a duplicate is not allowed under this Folder.
    """
    try:
        client = get_client()
        resp = await client.request("POST", f"/folder/{params.folder_id}/list", json_body=params.to_body())
        data = _extract_list_obj(resp.json())
        list_id = data.get("id", "unknown")
        name = data.get("name", params.name)
        return f"OK — created List **{name}** (id `{list_id}`) in folder `{params.folder_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_folderless_list",
    annotations=ToolAnnotations(
        title="Create Folderless List in Space",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_folderless_list(params: CreateFolderlessListInput) -> str:
    """Create a new List directly inside a Space, with no parent Folder.

    Use this for Spaces that are not organized into Folders. Use
    `clickup_create_list` instead when the List belongs under an existing
    Folder.

    When to Use:
    - The Space has no Folder structure and Lists sit directly under it.

    When NOT to Use:
    - The Space is organized into Folders — use `clickup_create_list` so the
      new List lands in the right Folder.
    - Copying an established List's tasks/views/custom fields — use
      `clickup_create_list_from_template_in_space` instead.

    Returns:
    A confirmation string with the new List's name and id, or an
    `Error ...` string.

    Examples:
    params = {"space_id": "67890", "name": "General"}
    params = {"space_id": "67890", "name": "Intake", "status": "green"}

    Error Handling:
    404 means space_id does not exist or is not accessible; 400 usually
    means `name` is missing or a duplicate is not allowed under this Space.
    """
    try:
        client = get_client()
        resp = await client.request("POST", f"/space/{params.space_id}/list", json_body=params.to_body())
        data = _extract_list_obj(resp.json())
        list_id = data.get("id", "unknown")
        name = data.get("name", params.name)
        return f"OK — created List **{name}** (id `{list_id}`) in space `{params.space_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_lists",
    annotations=ToolAnnotations(
        title="Get Lists in Folder",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_lists(params: GetListsInput) -> str:
    """List the Lists that belong to a Folder.

    When to Use:
    - Enumerating every List inside a specific Folder.

    When NOT to Use:
    - The Space has no Folders — use `clickup_get_folderless_lists`.
    - You already have the list_id and need full detail — use
      `clickup_get_list`.

    Returns:
    Markdown (default) or JSON per `response_format`.

    Pagination:
    ClickUp returns every List belonging to the Folder in one response; this
    tool then windows the result with `limit`/`offset` to keep responses
    small, reporting `has_more` and the next offset when applicable.

    Examples:
    params = {"folder_id": "12345"}
    params = {"folder_id": "12345", "archived": True, "limit": 50}

    Error Handling:
    404 means folder_id does not exist or is not accessible.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/folder/{params.folder_id}/list", params={"archived": params.archived})
        items = _extract_items(resp.json(), "lists")
        total = len(items)
        window = items[params.offset : params.offset + params.limit]
        return paginated_response(
            items=window,
            total=total,
            limit=params.limit,
            offset=params.offset,
            fmt=params.response_format,
            item_formatter=_format_list_item,
            title=f"Lists in Folder {params.folder_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_folderless_lists",
    annotations=ToolAnnotations(
        title="Get Folderless Lists in Space",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_folderless_lists(params: GetFolderlessListsInput) -> str:
    """List the Lists that live directly inside a Space (no Folder).

    When to Use:
    - Enumerating Lists in a Space that is not organized into Folders.

    When NOT to Use:
    - The Space uses Folders — use `clickup_get_lists` per Folder instead.
    - You already have the list_id and need full detail — use
      `clickup_get_list`.

    Returns:
    Markdown (default) or JSON per `response_format`.

    Pagination:
    ClickUp returns every folderless List in the Space in one response; this
    tool then windows the result with `limit`/`offset`, reporting `has_more`
    and the next offset when applicable.

    Examples:
    params = {"space_id": "67890"}
    params = {"space_id": "67890", "archived": True}

    Error Handling:
    404 means space_id does not exist or is not accessible.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/space/{params.space_id}/list", params={"archived": params.archived})
        items = _extract_items(resp.json(), "lists")
        total = len(items)
        window = items[params.offset : params.offset + params.limit]
        return paginated_response(
            items=window,
            total=total,
            limit=params.limit,
            offset=params.offset,
            fmt=params.response_format,
            item_formatter=_format_list_item,
            title=f"Folderless Lists in Space {params.space_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_list",
    annotations=ToolAnnotations(
        title="Get List",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_list(params: GetListInput) -> str:
    """Fetch full detail for a single List by id.

    When to Use:
    - You already know list_id and need its description, dates, color,
      priority, assignee, and parent Folder/Space.

    When NOT to Use:
    - Enumerating many Lists at once — use `clickup_get_lists` or
      `clickup_get_folderless_lists`.

    Returns:
    Markdown (default) or JSON per `response_format`.

    Examples:
    params = {"list_id": "901300123456"}
    params = {"list_id": "901300123456", "response_format": "json"}

    Error Handling:
    404 means list_id does not exist or is not accessible.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/list/{params.list_id}")
        data = _extract_list_obj(resp.json())
        return _format_list_detail(data, params.response_format)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_list",
    annotations=ToolAnnotations(
        title="Update List",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_list(params: UpdateListInput) -> str:
    """Update a List's name, description, dates, priority, assignee, or color.

    Only fields you explicitly set are sent — omitted fields are left
    unchanged by ClickUp. Supports `markdown_content` for a formatted
    description (in place of plain `content`), and `unset_status` to clear
    the List's color designation entirely.

    When to Use:
    - Renaming a List, changing its description, due date, priority, or
      owner, or changing/clearing its color.

    When NOT to Use:
    - Changing a Task's own status — that is a Task Status, unrelated to a
      List's color-only `status` field.

    Returns:
    A confirmation string with the updated List's name and id, or an
    `Error ...` string.

    Examples:
    params = {"list_id": "901300123456", "name": "Sprint 25"}
    params = {"list_id": "901300123456", "markdown_content": "## Updated scope", "priority": 1}
    params = {"list_id": "901300123456", "unset_status": True}

    Error Handling:
    404 means list_id does not exist; 400 usually means an invalid field
    value (e.g. an out-of-range priority).
    """
    try:
        body = params.to_body()
        if not body:
            return (
                "Error: no fields provided to update — supply at least one of name, content, markdown_content, "
                "due_date, due_date_time, priority, assignee, status, or unset_status."
            )
        client = get_client()
        resp = await client.request("PUT", f"/list/{params.list_id}", json_body=body)
        data = _extract_list_obj(resp.json())
        name = data.get("name", params.name or params.list_id)
        list_id = data.get("id", params.list_id)
        return f"OK — updated List **{name}** (id `{list_id}`)."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_list",
    annotations=ToolAnnotations(
        title="Delete List",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_list(params: DeleteListInput) -> str:
    """Permanently delete a List from the Workspace.

    This removes the List and all of its Tasks. There is no undo via the
    API — confirm with the caller before invoking this on production data.

    When to Use:
    - Removing a List that is no longer needed.

    When NOT to Use:
    - Temporarily hiding a List — archive it via `clickup_update_list`
      instead of deleting (archiving is not exposed as a dedicated flag on
      this endpoint set; use the ClickUp UI or the List's `archived` state
      through the Folder/Space update tools if you need reversible hiding).

    Returns:
    A confirmation string, or an `Error ...` string.

    Examples:
    params = {"list_id": "901300123456"}

    Error Handling:
    404 means list_id does not exist or was already deleted.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/list/{params.list_id}")
        return f"OK — deleted List `{params.list_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_add_task_to_list",
    annotations=ToolAnnotations(
        title="Add Task to List",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_add_task_to_list(params: AddTaskToListInput) -> str:
    """Add an existing Task's membership to an additional List.

    Requires the **Tasks in Multiple Lists** ClickApp to be enabled for the
    Workspace; without it ClickUp returns 403. This does not move the Task —
    it stays in its original (home) List and additionally appears in this
    one.

    When to Use:
    - A Task needs to appear in a second, cross-cutting List (e.g. a shared
      "This Sprint" List) without duplicating it.

    When NOT to Use:
    - Moving a Task to a different List entirely (removing it from its
      current List) — use the tasks module's move-task tool instead.
    - The Tasks in Multiple Lists ClickApp is disabled for the Workspace —
      enable it first (ClickUp → Settings → ClickApps).

    Returns:
    A confirmation string, or an `Error ...` string.

    Examples:
    params = {"list_id": "901300123456", "task_id": "abc123"}

    Error Handling:
    403 means the Tasks in Multiple Lists ClickApp is not enabled for this
    Workspace. 404 means list_id or task_id does not exist.
    """
    try:
        client = get_client()
        await client.request("POST", f"/list/{params.list_id}/task/{params.task_id}")
        return f"OK — added task `{params.task_id}` to list `{params.list_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_remove_task_from_list",
    annotations=ToolAnnotations(
        title="Remove Task from List",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_task_from_list(params: RemoveTaskFromListInput) -> str:
    """Remove a Task's membership from an additional (non-home) List.

    Requires the **Tasks in Multiple Lists** ClickApp to be enabled; without
    it ClickUp returns 403. You cannot remove a Task from its primary
    (home) List this way — only from additional Lists it was added to via
    `clickup_add_task_to_list`.

    When to Use:
    - Undoing a `clickup_add_task_to_list` call, or cleaning up a Task that
      no longer needs to appear in a secondary List.

    When NOT to Use:
    - Removing a Task entirely from its home List — that requires deleting
      the Task (see the tasks module) rather than this membership removal.

    Returns:
    A confirmation string, or an `Error ...` string.

    Examples:
    params = {"list_id": "901300123456", "task_id": "abc123"}

    Error Handling:
    403 means the Tasks in Multiple Lists ClickApp is not enabled, or you
    tried to remove the Task from its home List. 404 means list_id or
    task_id does not exist.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/list/{params.list_id}/task/{params.task_id}")
        return f"OK — removed task `{params.task_id}` from list `{params.list_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_list_from_template_in_folder",
    annotations=ToolAnnotations(
        title="Create List from Template in Folder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_list_from_template_in_folder(params: CreateListFromTemplateInFolderInput) -> str:
    """Create a new List inside a Folder by instantiating a List template.

    Use `clickup_get_list_templates` to discover available `template_id`
    values (they carry a `t-` prefix, e.g. `t-15363293`). With
    `return_immediately=True` (the default) the response's List id may
    represent a List that is still being populated in the background for
    large templates — poll with `clickup_get_list` if you need to confirm
    completion.

    When to Use:
    - Scaffolding a new List that should start with a known set of
      statuses, views, or starter Tasks defined in a template.

    When NOT to Use:
    - The Space has no Folders — use
      `clickup_create_list_from_template_in_space` instead.
    - No template fits — use `clickup_create_list` for a blank List.

    Returns:
    A confirmation string with the new List's id, or an `Error ...` string.

    Examples:
    params = {"folder_id": "12345", "template_id": "t-15363293", "name": "Sprint 24"}
    params = {"folder_id": "12345", "template_id": "t-15363293", "name": "Sprint 24", "return_immediately": False}

    Error Handling:
    400 means `name` is missing or already taken; 404 means the template,
    folder, or space was not found.
    """
    try:
        client = get_client()
        resp = await client.request(
            "POST",
            f"/folder/{params.folder_id}/list_template/{params.template_id}",
            json_body=params.to_body(),
        )
        data = _extract_list_obj(resp.json())
        list_id = data.get("id", "unknown")
        return (
            f"OK — creating List **{params.name}** from template `{params.template_id}` in folder "
            f"`{params.folder_id}` (list id `{list_id}`)."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_list_from_template_in_space",
    annotations=ToolAnnotations(
        title="Create List from Template in Space",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_list_from_template_in_space(params: CreateListFromTemplateInSpaceInput) -> str:
    """Create a new folderless List inside a Space by instantiating a template.

    Use `clickup_get_list_templates` to discover available `template_id`
    values (they carry a `t-` prefix, e.g. `t-15363293`). With
    `return_immediately=True` (the default) the response's List id may
    represent a List that is still being populated in the background for
    large templates — poll with `clickup_get_list` if you need to confirm
    completion.

    When to Use:
    - Scaffolding a new folderless List that should start with a known set
      of statuses, views, or starter Tasks defined in a template.

    When NOT to Use:
    - The target Space uses Folders — use
      `clickup_create_list_from_template_in_folder` instead.
    - No template fits — use `clickup_create_folderless_list` for a blank
      List.

    Returns:
    A confirmation string with the new List's id, or an `Error ...` string.

    Examples:
    params = {"space_id": "67890", "template_id": "t-15363293", "name": "General"}

    Error Handling:
    400 means `name` is missing or already taken; 404 means the template or
    space was not found.
    """
    try:
        client = get_client()
        resp = await client.request(
            "POST",
            f"/space/{params.space_id}/list_template/{params.template_id}",
            json_body=params.to_body(),
        )
        data = _extract_list_obj(resp.json())
        list_id = data.get("id", "unknown")
        return (
            f"OK — creating List **{params.name}** from template `{params.template_id}` in space "
            f"`{params.space_id}` (list id `{list_id}`)."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_list_templates",
    annotations=ToolAnnotations(
        title="Get List Templates",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_list_templates(params: GetListTemplatesInput) -> str:
    """List available List templates for a Workspace.

    The `t-` prefixed `id` field returned here feeds the `template_id`
    parameter of `clickup_create_list_from_template_in_folder` and
    `clickup_create_list_from_template_in_space`.

    When to Use:
    - Discovering which List templates exist before instantiating one.

    When NOT to Use:
    - You already know the template_id — go straight to
      `clickup_create_list_from_template_in_folder`/`_in_space`.

    Returns:
    Markdown (default) or JSON per `response_format`.

    Pagination:
    ClickUp returns every template in one response; this tool then windows
    the result with `limit`/`offset`.

    Examples:
    params = {}
    params = {"team_id": "90130012345", "limit": 50}

    Error Handling:
    Raises a configuration error if team_id is omitted and CLICKUP_TEAM_ID
    is not set; 401/403 indicate a token or plan issue.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/list_template")
        items = _extract_items(resp.json(), "templates")
        total = len(items)
        window = items[params.offset : params.offset + params.limit]
        return paginated_response(
            items=window,
            total=total,
            limit=params.limit,
            offset=params.offset,
            fmt=params.response_format,
            item_formatter=_format_template_item,
            title=f"List Templates for Workspace {team_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)
