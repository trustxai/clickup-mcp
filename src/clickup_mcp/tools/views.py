"""Views tool module — create/list/get/update/delete task and page views.

ClickUp views live at four Hierarchy levels — the Everything Level of a
Workspace ("team"), a Space, a Folder, and a List — plus standalone
operations on a single view by id (get/get-tasks/update/delete). Wave 1 —
task t4-views.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Context-window guards, independent of any API-side paging.
MAX_DISPLAY_VIEWS = 100
MAX_DISPLAY_TASKS = 100

_VIEW_TYPE_NOTE = "list, board, calendar, table, timeline, workload, activity, map, conversation, or gantt."


class ViewType(StrEnum):
    """ClickUp task/page view types."""

    LIST = "list"
    BOARD = "board"
    CALENDAR = "calendar"
    TABLE = "table"
    TIMELINE = "timeline"
    WORKLOAD = "workload"
    ACTIVITY = "activity"
    MAP = "map"
    CONVERSATION = "conversation"
    GANTT = "gantt"


class _ViewPayload(BaseModel):
    """Shared free-form config blocks accepted by every create/update view body.

    ClickUp's view schema groups its configuration into these seven blocks.
    Every create/update endpoint expects all seven keys in the body, but an
    empty object for a block tells ClickUp to fall back to its own default —
    so callers only need to populate the parts they actually want to set.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    grouping: dict[str, Any] = Field(
        default_factory=dict,
        description="Row grouping, e.g. {'field': 'status', 'dir': 1, 'collapsed': []}.",
    )
    divide: dict[str, Any] = Field(
        default_factory=dict,
        description="Split-view (divide) config, e.g. {'field': 'assignee'}.",
    )
    sorting: dict[str, Any] = Field(
        default_factory=dict,
        description="Sort order, e.g. {'fields': [{'field': 'dueDate', 'dir': 1}]}.",
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Filter rules, e.g. {'op': 'AND', 'fields': [...]}.",
    )
    columns: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Visible custom-field columns, e.g. {'fields': [...]}. Custom Fields added to a "
            "view at the Everything Level apply to every task in the Workspace and, once "
            "added, that view can no longer be moved to another Hierarchy level."
        ),
    )
    team_sidebar: dict[str, Any] = Field(
        default_factory=dict,
        description="Sidebar assignee/tag rollup config, e.g. {'assignees': [...], 'assigned_comments': True}.",
    )
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Display toggles, e.g. {'show_task_locations': False, 'show_subtasks': 3}.",
    )

    def to_body(self) -> dict[str, Any]:
        """Serialize the seven shared config blocks for the request body."""
        return {
            "grouping": self.grouping,
            "divide": self.divide,
            "sorting": self.sorting,
            "filters": self.filters,
            "columns": self.columns,
            "team_sidebar": self.team_sidebar,
            "settings": self.settings,
        }


class CreateTeamViewInput(_ViewPayload):
    """Input for creating a view at the Everything Level of a Workspace."""

    team_id: str = Field(..., min_length=1, description="Workspace (team) id to create the view in.")
    name: str = Field(..., min_length=1, description="Display name for the new view.")
    type: ViewType = Field(..., description=f"View type to create: {_VIEW_TYPE_NOTE}")


class CreateSpaceViewInput(_ViewPayload):
    """Input for creating a view scoped to a Space."""

    space_id: str = Field(..., min_length=1, description="Space id to create the view in.")
    name: str = Field(..., min_length=1, description="Display name for the new view.")
    type: ViewType = Field(..., description=f"View type to create: {_VIEW_TYPE_NOTE}")


class CreateFolderViewInput(_ViewPayload):
    """Input for creating a view scoped to a Folder."""

    folder_id: str = Field(..., min_length=1, description="Folder id to create the view in.")
    name: str = Field(..., min_length=1, description="Display name for the new view.")
    type: ViewType = Field(..., description=f"View type to create: {_VIEW_TYPE_NOTE}")


class CreateListViewInput(_ViewPayload):
    """Input for creating a view scoped to a List."""

    list_id: str = Field(..., min_length=1, description="List id to create the view in.")
    name: str = Field(..., min_length=1, description="Display name for the new view.")
    type: ViewType = Field(..., description=f"View type to create: {_VIEW_TYPE_NOTE}")


class GetTeamViewsInput(BaseModel):
    """Input for listing Everything-Level views of a Workspace."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(..., min_length=1, description="Workspace (team) id to list views for.")
    response_format: ResponseFormat = Field(ResponseFormat.MARKDOWN, description="Output format.")


class GetSpaceViewsInput(BaseModel):
    """Input for listing the views of a Space."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="Space id to list views for.")
    response_format: ResponseFormat = Field(ResponseFormat.MARKDOWN, description="Output format.")


class GetFolderViewsInput(BaseModel):
    """Input for listing the views of a Folder."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., min_length=1, description="Folder id to list views for.")
    response_format: ResponseFormat = Field(ResponseFormat.MARKDOWN, description="Output format.")


class GetListViewsInput(BaseModel):
    """Input for listing the views of a List."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., min_length=1, description="List id to list views for.")
    response_format: ResponseFormat = Field(ResponseFormat.MARKDOWN, description="Output format.")


class GetViewInput(BaseModel):
    """Input for fetching a single view's full configuration."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    view_id: str = Field(..., min_length=1, description="View id to fetch.")
    response_format: ResponseFormat = Field(ResponseFormat.MARKDOWN, description="Output format.")


class GetViewTasksInput(BaseModel):
    """Input for listing the tasks currently visible in a view."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    view_id: str = Field(..., min_length=1, description="View id whose visible tasks to list.")
    page: int = Field(0, ge=0, description="0-indexed page number; ClickUp returns up to 100 tasks per page.")
    response_format: ResponseFormat = Field(ResponseFormat.MARKDOWN, description="Output format.")


class UpdateViewInput(_ViewPayload):
    """Input for replacing a view's name, parent, and full config.

    ClickUp's update endpoint replaces the entire view body — there is no
    partial/PATCH semantics — so `parent_id`/`parent_type` and every config
    block must be resupplied even if unchanged.
    """

    view_id: str = Field(..., min_length=1, description="View id to update.")
    name: str = Field(..., min_length=1, description="New display name for the view.")
    type: ViewType = Field(..., description=f"View type: {_VIEW_TYPE_NOTE}")
    parent_id: str = Field(..., min_length=1, description="Id of the view's parent (team/space/folder/list).")
    parent_type: Literal[4, 5, 6, 7] = Field(
        ...,
        description="Parent Hierarchy level: 4=Space, 5=Folder, 6=List, 7=Everything (Workspace).",
    )


class DeleteViewInput(BaseModel):
    """Input for permanently deleting a view."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    view_id: str = Field(..., min_length=1, description="View id to delete.")


def _extract_views(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the `views` array regardless of surrounding response shape."""
    views = payload.get("views")
    return views if isinstance(views, list) else []


def _extract_required_views(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Folder/List responses also carry a `required_views` object of built-in views."""
    required = payload.get("required_views")
    if isinstance(required, dict):
        return [v for v in required.values() if isinstance(v, dict)]
    return []


def _format_view_line(view: dict[str, Any]) -> str:
    view_id = view.get("id", "unknown")
    name = view.get("name", "Untitled")
    vtype = view.get("type", "unknown")
    parent = view.get("parent") or {}
    parent_id = parent.get("id", "—")
    protected_note = " (protected)" if view.get("protected") else ""
    return f"- **{name}** — id `{view_id}`, type `{vtype}`, parent `{parent_id}`{protected_note}"


def _format_views_response(
    views: list[dict[str, Any]],
    required_views: list[dict[str, Any]],
    fmt: ResponseFormat,
    title: str,
) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json({"views": views, "required_views": required_views})

    lines = [f"# {title}", "", f"Found **{len(views)}** view(s)."]
    if len(views) > MAX_DISPLAY_VIEWS:
        lines.append(f"_Showing first {MAX_DISPLAY_VIEWS} of {len(views)}._")
    lines.append("")
    for view in views[:MAX_DISPLAY_VIEWS]:
        lines.append(_format_view_line(view))
    if not views:
        lines.append("_No views found._")
    if required_views:
        lines.append("")
        lines.append("Built-in (required) views for this location:")
        for view in required_views:
            lines.append(_format_view_line(view))
    return "\n".join(lines)


def _format_view_detail(view: dict[str, Any], fmt: ResponseFormat) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json(view)

    view_id = view.get("id", "unknown")
    name = view.get("name", "Untitled")
    vtype = view.get("type", "unknown")
    parent = view.get("parent") or {}
    date_created = epoch_to_human(view.get("date_created"))
    config = {
        key: view.get(key)
        for key in ("grouping", "divide", "sorting", "filters", "columns", "team_sidebar", "settings")
    }
    lines = [
        f"# View: {name}",
        "",
        f"- id: `{view_id}`",
        f"- type: `{vtype}`",
        f"- parent: id `{parent.get('id', '—')}`, type `{parent.get('type', '—')}`",
        f"- created: {date_created}",
        "",
        "Full config (grouping/divide/sorting/filters/columns/team_sidebar/settings):",
        "",
        "```json",
        to_json(config),
        "```",
    ]
    return "\n".join(lines)


def _format_task_line(task: dict[str, Any]) -> str:
    task_id = task.get("id", "unknown")
    name = task.get("name", "Untitled")
    status = (task.get("status") or {}).get("status", "unknown")
    return f"- **{name}** — id `{task_id}`, status `{status}`"


def _format_view_tasks_response(tasks: list[dict[str, Any]], page: int, last_page: bool, fmt: ResponseFormat) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json({"tasks": tasks, "page": page, "last_page": last_page})

    lines = [f"# View Tasks — page {page}", "", f"**{len(tasks)}** task(s) returned."]
    if not last_page:
        lines.append(f"More available — call again with page={page + 1}.")
    lines.append("")
    for task in tasks[:MAX_DISPLAY_TASKS]:
        lines.append(_format_task_line(task))
    if not tasks:
        lines.append("_No tasks visible in this view._")
    return "\n".join(lines)


@mcp.tool(
    name="clickup_create_team_view",
    annotations=ToolAnnotations(
        title="Create Workspace (Everything-Level) View",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_team_view(params: CreateTeamViewInput) -> str:
    """Create a task or page view at the Everything Level of a Workspace.

    Everything-Level views can surface tasks from every Space in the
    Workspace, which makes this the level to use for cross-Space dashboards.

    When to Use:
    - Building a Workspace-wide view (e.g. a Board across every Space).
    - The view needs Custom Field columns that should apply to every task in
      the Workspace — see the `columns` field note.

    When NOT to Use:
    - The view only needs tasks from one Space/Folder/List — use
      `clickup_create_space_view` / `clickup_create_folder_view` /
      `clickup_create_list_view`, which scope the view naturally.

    Returns:
    A confirmation string with the new view's id, name, and type, or an
    `Error ...` string.

    Examples:
    params = {
        "team_id": "123",
        "name": "Everything Board",
        "type": "board",
        "grouping": {"field": "status"},
    }

    Error Handling:
    404 means the Workspace (team) id is wrong; 403 can mean the token lacks
    access to this Workspace.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name, "type": params.type.value, **params.to_body()}
        resp = await client.request("POST", f"/team/{params.team_id}/view", json_body=body)
        view = resp.json().get("view", {})
        view_id = view.get("id", "unknown")
        return (
            f"Created **{params.type.value}** view **{params.name}** (id `{view_id}`) "
            f"at the Everything Level of Workspace {params.team_id}."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_space_view",
    annotations=ToolAnnotations(
        title="Create Space View",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_space_view(params: CreateSpaceViewInput) -> str:
    """Create a task or page view scoped to a Space.

    When to Use:
    - Building a dashboard/board/calendar for one Space specifically.

    When NOT to Use:
    - A Workspace-wide view — use `clickup_create_team_view`.
    - A Folder- or List-scoped view — use `clickup_create_folder_view` /
      `clickup_create_list_view`.

    Returns:
    A confirmation string with the new view's id, name, and type, or an
    `Error ...` string.

    Examples:
    params = {"space_id": "456", "name": "Sprint Board", "type": "board"}

    Error Handling:
    404 means the Space id is wrong; 403 can mean the token lacks access to
    this Space.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name, "type": params.type.value, **params.to_body()}
        resp = await client.request("POST", f"/space/{params.space_id}/view", json_body=body)
        view = resp.json().get("view", {})
        view_id = view.get("id", "unknown")
        return f"Created **{params.type.value}** view **{params.name}** (id `{view_id}`) in Space {params.space_id}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_folder_view",
    annotations=ToolAnnotations(
        title="Create Folder View",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_folder_view(params: CreateFolderViewInput) -> str:
    """Create a task or page view scoped to a Folder.

    When to Use:
    - Building a dashboard/board/calendar for one Folder specifically.

    When NOT to Use:
    - A Space- or Workspace-wide view — use `clickup_create_space_view` /
      `clickup_create_team_view`.
    - A List-scoped view — use `clickup_create_list_view`.

    Returns:
    A confirmation string with the new view's id, name, and type, or an
    `Error ...` string.

    Examples:
    params = {"folder_id": "789", "name": "Backlog Table", "type": "table"}

    Error Handling:
    404 means the Folder id is wrong.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name, "type": params.type.value, **params.to_body()}
        resp = await client.request("POST", f"/folder/{params.folder_id}/view", json_body=body)
        view = resp.json().get("view", {})
        view_id = view.get("id", "unknown")
        return f"Created **{params.type.value}** view **{params.name}** (id `{view_id}`) in Folder {params.folder_id}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_list_view",
    annotations=ToolAnnotations(
        title="Create List View",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_list_view(params: CreateListViewInput) -> str:
    """Create a task or page view scoped to a List.

    When to Use:
    - Building a dashboard/board/calendar for one List specifically.

    When NOT to Use:
    - A Folder-, Space-, or Workspace-wide view — use
      `clickup_create_folder_view` / `clickup_create_space_view` /
      `clickup_create_team_view`.

    Returns:
    A confirmation string with the new view's id, name, and type, or an
    `Error ...` string.

    Examples:
    params = {"list_id": "901", "name": "Sprint Calendar", "type": "calendar"}

    Error Handling:
    404 means the List id is wrong.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name, "type": params.type.value, **params.to_body()}
        resp = await client.request("POST", f"/list/{params.list_id}/view", json_body=body)
        view = resp.json().get("view", {})
        view_id = view.get("id", "unknown")
        return f"Created **{params.type.value}** view **{params.name}** (id `{view_id}`) in List {params.list_id}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_team_views",
    annotations=ToolAnnotations(
        title="Get Workspace (Everything-Level) Views",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_team_views(params: GetTeamViewsInput) -> str:
    """List the task and page views defined at the Everything Level of a Workspace.

    When to Use:
    - Discovering existing Workspace-wide views (and their ids) before calling
      `clickup_get_view`, `clickup_get_view_tasks`, or `clickup_update_view`.

    When NOT to Use:
    - Views scoped to one Space/Folder/List — use `clickup_get_space_views` /
      `clickup_get_folder_views` / `clickup_get_list_views`.

    Returns:
    Markdown (default) or JSON listing of views: id, name, type, parent.

    Examples:
    params = {"team_id": "123"}

    Error Handling:
    404 means the Workspace (team) id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/team/{params.team_id}/view")
        payload = resp.json()
        views = _extract_views(payload)
        required_views = _extract_required_views(payload)
        title = f"Everything-Level Views — Workspace {params.team_id}"
        return _format_views_response(views, required_views, params.response_format, title)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_space_views",
    annotations=ToolAnnotations(
        title="Get Space Views",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_space_views(params: GetSpaceViewsInput) -> str:
    """List the task and page views available for a Space.

    When to Use:
    - Discovering existing Space-level views (and their ids) before calling
      `clickup_get_view`, `clickup_get_view_tasks`, or `clickup_update_view`.

    When NOT to Use:
    - Everything-, Folder-, or List-level views — use `clickup_get_team_views` /
      `clickup_get_folder_views` / `clickup_get_list_views`.

    Returns:
    Markdown (default) or JSON listing of views: id, name, type, parent.

    Examples:
    params = {"space_id": "456"}

    Error Handling:
    404 means the Space id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/space/{params.space_id}/view")
        payload = resp.json()
        views = _extract_views(payload)
        required_views = _extract_required_views(payload)
        title = f"Views — Space {params.space_id}"
        return _format_views_response(views, required_views, params.response_format, title)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_folder_views",
    annotations=ToolAnnotations(
        title="Get Folder Views",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_folder_views(params: GetFolderViewsInput) -> str:
    """List the task and page views available for a Folder.

    When to Use:
    - Discovering existing Folder-level views (and their ids) before calling
      `clickup_get_view`, `clickup_get_view_tasks`, or `clickup_update_view`.

    When NOT to Use:
    - Everything-, Space-, or List-level views — use `clickup_get_team_views` /
      `clickup_get_space_views` / `clickup_get_list_views`.

    Returns:
    Markdown (default) or JSON listing of views: id, name, type, parent, plus
    any built-in `required_views` (e.g. the default List/Board view).

    Examples:
    params = {"folder_id": "789"}

    Error Handling:
    404 means the Folder id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/folder/{params.folder_id}/view")
        payload = resp.json()
        views = _extract_views(payload)
        required_views = _extract_required_views(payload)
        title = f"Views — Folder {params.folder_id}"
        return _format_views_response(views, required_views, params.response_format, title)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_list_views",
    annotations=ToolAnnotations(
        title="Get List Views",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_list_views(params: GetListViewsInput) -> str:
    """List the task and page views available for a List.

    ClickUp returns custom views and the List's built-in ("required") views —
    e.g. the default List view — as separate arrays; both are shown here.

    When to Use:
    - Discovering existing List-level views (and their ids) before calling
      `clickup_get_view`, `clickup_get_view_tasks`, or `clickup_update_view`.

    When NOT to Use:
    - Everything-, Space-, or Folder-level views — use `clickup_get_team_views` /
      `clickup_get_space_views` / `clickup_get_folder_views`.

    Returns:
    Markdown (default) or JSON listing of views: id, name, type, parent, plus
    any built-in `required_views`.

    Examples:
    params = {"list_id": "901"}

    Error Handling:
    404 means the List id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/list/{params.list_id}/view")
        payload = resp.json()
        views = _extract_views(payload)
        required_views = _extract_required_views(payload)
        title = f"Views — List {params.list_id}"
        return _format_views_response(views, required_views, params.response_format, title)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_view",
    annotations=ToolAnnotations(
        title="Get View",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_view(params: GetViewInput) -> str:
    """Get the full configuration of a single task or page view.

    The information returned varies by view type; this always includes the
    seven shared config blocks (grouping/divide/sorting/filters/columns/
    team_sidebar/settings) needed as the starting point for `clickup_update_view`.

    When to Use:
    - Inspecting a view's current config before updating it.
    - Confirming a view's parent Hierarchy level (id + type) prior to a
      `clickup_update_view` call, which requires re-sending `parent_id`/`parent_type`.

    When NOT to Use:
    - Listing every view at a location — use `clickup_get_team_views` /
      `clickup_get_space_views` / `clickup_get_folder_views` / `clickup_get_list_views`.
    - Listing the tasks a view shows — use `clickup_get_view_tasks`.

    Returns:
    Markdown (default, includes a JSON config block) or full JSON.

    Examples:
    params = {"view_id": "abc123"}

    Error Handling:
    404 means the view id is wrong or the view was deleted.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/view/{params.view_id}")
        view = resp.json().get("view", {})
        return _format_view_detail(view, params.response_format)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_view_tasks",
    annotations=ToolAnnotations(
        title="Get Tasks Visible In a View",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_view_tasks(params: GetViewTasksInput) -> str:
    """List the tasks currently visible in a view, honoring its filters and sorting.

    Unlike `clickup_get_tasks` (which lists all tasks in a List) or
    `clickup_get_filtered_team_tasks` (workspace-wide filters), this returns
    exactly what the view itself shows — respecting the view's own grouping,
    sorting, and filter configuration.

    When to Use:
    - Reproducing exactly what a saved Board/Table/Calendar view displays.

    When NOT to Use:
    - Filtering tasks with ad-hoc criteria not tied to a saved view — use
      `clickup_get_filtered_team_tasks` instead.

    Returns:
    Markdown (default) or JSON with the page's tasks (id, name, status) and
    whether more pages remain.

    Pagination:
    ClickUp pages `page`, 0-indexed, up to 100 tasks per page. Check
    `last_page` (JSON) or the "More available" note (markdown) and call again
    with `page + 1` until it is true.

    Examples:
    params = {"view_id": "abc123", "page": 0}

    Error Handling:
    404 means the view id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/view/{params.view_id}/task", params={"page": params.page})
        payload = resp.json()
        tasks = payload.get("tasks") or []
        last_page = bool(payload.get("last_page", True))
        return _format_view_tasks_response(tasks, params.page, last_page, params.response_format)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_view",
    annotations=ToolAnnotations(
        title="Update View",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_view(params: UpdateViewInput) -> str:
    """Rename a view and/or replace its grouping/sorting/filters/columns/settings.

    ClickUp's `PUT /view/{view_id}` replaces the *entire* view body — there is
    no partial-update semantics. Call `clickup_get_view` first and use its
    JSON config block as the starting point for every field this tool
    exposes (including `parent_id`/`parent_type`), so unrelated config is not
    silently reset to empty.

    When to Use:
    - Renaming a view, or changing its grouping/sorting/filters/columns/settings.

    When NOT to Use:
    - Moving a view to a different Space/Folder/List — ClickUp views cannot
      change Hierarchy level; delete and recreate instead (`clickup_delete_view`
      + the matching `clickup_create_*_view`).

    Returns:
    A confirmation string with the updated view's id/name, or an
    `Error ...` string.

    Examples:
    params = {
        "view_id": "abc123",
        "name": "Sprint Board (renamed)",
        "type": "board",
        "parent_id": "901",
        "parent_type": 6,
        "grouping": {"field": "assignee"},
    }

    Error Handling:
    404 means the view id is wrong; 400 usually means a malformed
    grouping/filters/sorting/columns/settings block.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {
            "name": params.name,
            "type": params.type.value,
            "parent": {"id": params.parent_id, "type": params.parent_type},
            **params.to_body(),
        }
        resp = await client.request("PUT", f"/view/{params.view_id}", json_body=body)
        view = resp.json().get("view", {})
        view_id = view.get("id", params.view_id)
        name = view.get("name", params.name)
        return f"Updated view **{name}** (id `{view_id}`)."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_view",
    annotations=ToolAnnotations(
        title="Delete View",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_view(params: DeleteViewInput) -> str:
    """Permanently delete a task or page view.

    When to Use:
    - Removing a view (dashboard/board/etc.) that is no longer needed.

    When NOT to Use:
    - Deleting the underlying List/Folder/Space — this only removes the view
      itself, not its parent container (use the List/Folder/Space delete
      tools for that).

    Returns:
    A confirmation string, or an `Error ...` string.

    Examples:
    params = {"view_id": "abc123"}

    Error Handling:
    404 means the view id is already gone or was never valid.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/view/{params.view_id}")
        return f"Deleted view id `{params.view_id}`."
    except Exception as exc:
        return handle_api_error(exc)
