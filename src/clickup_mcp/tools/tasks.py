"""Task tools — create/read/update/delete tasks plus workspace-wide filtering,
move, merge, templates, and time-in-status.

This is the highest-traffic module in the server. The two read tools cover
different scopes and callers should not confuse them:

- ``clickup_get_tasks`` lists tasks inside **one List** (`GET /list/{id}/task`).
- ``clickup_get_filtered_team_tasks`` searches **the whole Workspace** across many
  Spaces/Folders/Lists at once (`GET /team/{id}/task`).

Wave workers copy `tools/health.py`'s shape; this module extends it with pydantic
input models, dual markdown/JSON output, page-based pagination, and the shared
`custom_task_ids` + `team_id` query pair.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Display cap independent of API paging, to keep tool output under the MCP size
# limit even when a List/Workspace returns a full 100-task page.
MAX_DISPLAY_TASKS = 50

# ClickUp returns up to 100 tasks per page on both list endpoints.
_PAGE_SIZE = 100

PriorityLevel = Literal[1, 2, 3, 4]  # 1=Urgent, 2=High, 3=Normal, 4=Low
OrderBy = Literal["id", "created", "updated", "due_date"]


# --------------------------------------------------------------------------- #
# Small query/body builders shared by the models below.
# --------------------------------------------------------------------------- #
def _put(target: dict[str, Any], key: str, value: Any) -> None:
    """Add a scalar to a query/body dict only when it was supplied (not None)."""
    if value is not None:
        target[key] = value


def _put_list(target: dict[str, Any], key: str, value: list[Any] | None) -> None:
    """Add an array to a query/body dict only when it is non-empty."""
    if value:
        target[key] = value


def _compact_json(value: Any) -> str:
    """URL-safe compact JSON (used for the ``custom_fields`` filter query param)."""
    return json.dumps(value, separators=(",", ":"))


def _resolve_workspace_id(explicit: str | None) -> str:
    """Return the Workspace (team) id, falling back to CLICKUP_TEAM_ID.

    Raised as `RuntimeError` so `handle_api_error` renders a clean `Error: ...`.
    """
    resolved = explicit or get_settings().clickup_team_id
    if not resolved:
        raise RuntimeError("team_id is required: pass it explicitly or set CLICKUP_TEAM_ID in the environment/.env.")
    return resolved


# --------------------------------------------------------------------------- #
# Formatting helpers.
# --------------------------------------------------------------------------- #
def _task_summary(task: dict[str, Any]) -> str:
    """One-line markdown bullet describing a task."""
    tid = task.get("id", "?")
    name = task.get("name") or "(no name)"
    status = (task.get("status") or {}).get("status", "unknown")
    assignees = ", ".join(a.get("username") or str(a.get("id")) for a in task.get("assignees") or []) or "unassigned"
    parts = [f"- **{name}** (`{tid}`) — status: {status}; assignees: {assignees}"]
    custom_id = task.get("custom_id")
    if custom_id:
        parts.append(f"; custom_id: {custom_id}")
    due = task.get("due_date")
    if due:
        parts.append(f"; due: {epoch_to_human(due)}")
    url = task.get("url")
    if url:
        parts.append(f"; {url}")
    return "".join(parts)


def _format_task_list(
    tasks: list[dict[str, Any]],
    *,
    page: int,
    has_more: bool,
    fmt: ResponseFormat,
    title: str,
) -> str:
    """Uniform page-based rendering for the two task-list tools."""
    display = tasks[:MAX_DISPLAY_TASKS]
    truncated = len(tasks) > MAX_DISPLAY_TASKS

    if fmt is ResponseFormat.JSON:
        return to_json(
            {
                "title": title,
                "page": page,
                "count": len(tasks),
                "displayed": len(display),
                "truncated": truncated,
                "has_more": has_more,
                "tasks": display,
            }
        )

    lines = [f"# {title}", "", f"Page **{page}** — **{len(tasks):,}** task(s) returned."]
    if has_more:
        lines.append(f"More available — request page **{page + 1}**.")
    if truncated:
        lines.append(f"_Showing the first {MAX_DISPLAY_TASKS} of {len(tasks):,} to stay under the size limit._")
    lines.append("")
    for task in display:
        lines.append(_task_summary(task))
    if not display:
        lines.append("_No tasks matched._")
    return "\n".join(lines)


def _format_single_task(task: dict[str, Any], fmt: ResponseFormat) -> str:
    """Detailed rendering for a single fetched task."""
    if fmt is ResponseFormat.JSON:
        return to_json(task)

    status = (task.get("status") or {}).get("status", "unknown")
    priority = (task.get("priority") or {}).get("priority") if isinstance(task.get("priority"), dict) else None
    assignees = ", ".join(a.get("username") or str(a.get("id")) for a in task.get("assignees") or []) or "unassigned"
    lines = [
        f"# {task.get('name') or '(no name)'}",
        "",
        f"- **id:** `{task.get('id', '?')}`" + (f" (custom_id: {task['custom_id']})" if task.get("custom_id") else ""),
        f"- **status:** {status}",
        f"- **priority:** {priority or 'none'}",
        f"- **assignees:** {assignees}",
        f"- **due date:** {epoch_to_human(task.get('due_date'))}",
        f"- **start date:** {epoch_to_human(task.get('start_date'))}",
        f"- **url:** {task.get('url', 'n/a')}",
    ]
    description = task.get("markdown_description") or task.get("description")
    if description:
        lines += ["", "## Description", "", str(description)]
    return "\n".join(lines)


def _minutes_to_human(minutes: Any) -> str:
    """Render a `by_minute` figure as `Xh Ym`."""
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return "N/A"
    hours, mins = divmod(total, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _time_in_status_markdown(data: dict[str, Any], title: str) -> str:
    """Render one task's time-in-status payload as markdown."""
    lines = [f"# {title}", ""]
    current = data.get("current_status") or {}
    if current:
        by_minute = (current.get("total_time") or {}).get("by_minute")
        lines.append(f"**Current status:** {current.get('status', '?')} ({_minutes_to_human(by_minute)})")
    history = data.get("status_history") or []
    if history:
        lines += ["", "**History:**"]
        for entry in history:
            by_minute = (entry.get("total_time") or {}).get("by_minute")
            lines.append(f"- {entry.get('status', '?')}: {_minutes_to_human(by_minute)}")
    if not current and not history:
        lines.append("_No time-in-status data (enable the 'Total time in Status' ClickApp)._")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Shared input mixin — the custom_task_ids + team_id query pair.
# --------------------------------------------------------------------------- #
class _CustomTaskIdParams(BaseModel):
    """Mixin for tools that accept a task id which may be a *custom* task id.

    ClickUp task ids are normally the short internal ids (e.g. `86cxy1`). To
    address a task by the human-friendly Custom Task ID (e.g. `PROJ-123`) set
    `custom_task_ids=true`; ClickUp then also requires `team_id` (the Workspace
    id), which falls back to CLICKUP_TEAM_ID when omitted.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    custom_task_ids: bool = Field(
        default=False,
        description="Set true to treat task_id as a Custom Task ID (e.g. 'PROJ-123') instead of the internal id.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace id; required by ClickUp when custom_task_ids=true. Falls back to CLICKUP_TEAM_ID.",
    )

    def id_query(self) -> dict[str, Any]:
        """Query params for the custom-task-id pair (empty when not enabled)."""
        if not self.custom_task_ids:
            return {}
        params: dict[str, Any] = {"custom_task_ids": True}
        team = self.team_id or get_settings().clickup_team_id
        if team:
            params["team_id"] = team
        return params


# --------------------------------------------------------------------------- #
# Input models.
# --------------------------------------------------------------------------- #
class CreateTaskInput(BaseModel):
    """Body + path for creating a task in a List."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(description="Id of the List the task is created in.")
    name: str = Field(min_length=1, description="Task name (required).")
    description: str | None = Field(default=None, description="Plain-text description.")
    markdown_content: str | None = Field(
        default=None,
        description="Markdown description. Takes precedence over `description` when both are given.",
    )
    assignees: list[int] | None = Field(default=None, description="User ids to assign.")
    group_assignees: list[str] | None = Field(default=None, description="User-group ids to assign.")
    tags: list[str] | None = Field(default=None, description="Tag names to attach (must already exist in the Space).")
    status: str | None = Field(default=None, description="Status name; defaults to the List's first status.")
    priority: PriorityLevel | None = Field(default=None, description="1=Urgent, 2=High, 3=Normal, 4=Low.")
    due_date: int | None = Field(default=None, description="Due date as a unix timestamp in milliseconds.")
    due_date_time: bool | None = Field(default=None, description="True if due_date carries a time component.")
    start_date: int | None = Field(default=None, description="Start date as a unix timestamp in milliseconds.")
    start_date_time: bool | None = Field(default=None, description="True if start_date carries a time component.")
    time_estimate: int | None = Field(default=None, description="Time estimate in milliseconds.")
    points: float | None = Field(default=None, description="Sprint points.")
    notify_all: bool | None = Field(default=None, description="Notify all assignees of the creation.")
    parent: str | None = Field(default=None, description="Parent task id — set to create this task as a subtask.")
    links_to: str | None = Field(default=None, description="Task id to link this new task to.")
    check_required_custom_fields: bool | None = Field(
        default=None, description="Enforce the List's required custom fields."
    )
    custom_item_id: int | None = Field(
        default=None, description="Custom task type id (0 = standard task, 1 = Milestone)."
    )
    custom_fields: list[dict[str, Any]] | None = Field(
        default=None,
        description="Custom field values as objects: [{'id': '<field-uuid>', 'value': ...}].",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name}
        _put(body, "description", self.description)
        _put(body, "markdown_content", self.markdown_content)
        _put_list(body, "assignees", self.assignees)
        _put_list(body, "group_assignees", self.group_assignees)
        _put_list(body, "tags", self.tags)
        _put(body, "status", self.status)
        _put(body, "priority", self.priority)
        _put(body, "due_date", self.due_date)
        _put(body, "due_date_time", self.due_date_time)
        _put(body, "start_date", self.start_date)
        _put(body, "start_date_time", self.start_date_time)
        _put(body, "time_estimate", self.time_estimate)
        _put(body, "points", self.points)
        _put(body, "notify_all", self.notify_all)
        _put(body, "parent", self.parent)
        _put(body, "links_to", self.links_to)
        _put(body, "check_required_custom_fields", self.check_required_custom_fields)
        _put(body, "custom_item_id", self.custom_item_id)
        _put_list(body, "custom_fields", self.custom_fields)
        return body


class GetTaskInput(_CustomTaskIdParams):
    """Path + query for fetching a single task."""

    task_id: str = Field(description="Task id (or Custom Task ID when custom_task_ids=true).")
    include_subtasks: bool | None = Field(default=None, description="Include the task's subtasks in the response.")
    include_markdown_description: bool | None = Field(
        default=None, description="Return the description as markdown (`markdown_description`)."
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")

    def query(self) -> dict[str, Any]:
        params = self.id_query()
        _put(params, "include_subtasks", self.include_subtasks)
        _put(params, "include_markdown_description", self.include_markdown_description)
        return params


class GetTasksInput(BaseModel):
    """Path + full filter surface for listing tasks in one List."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(description="Id of the List to read tasks from.")
    page: int = Field(default=0, ge=0, description="0-indexed page; up to 100 tasks per page.")
    order_by: OrderBy | None = Field(default=None, description="Sort field: id | created | updated | due_date.")
    reverse: bool | None = Field(default=None, description="Reverse the sort order.")
    subtasks: bool | None = Field(default=None, description="Include subtasks.")
    statuses: list[str] | None = Field(default=None, description="Filter to these status names.")
    include_closed: bool | None = Field(default=None, description="Include tasks in closed statuses.")
    archived: bool | None = Field(default=None, description="Return archived tasks.")
    include_markdown_description: bool | None = Field(default=None, description="Return descriptions as markdown.")
    assignees: list[str] | None = Field(default=None, description="Filter by assignee user ids.")
    tags: list[str] | None = Field(default=None, description="Filter by tag names.")
    due_date_gt: int | None = Field(default=None, description="Due date after this unix-ms timestamp.")
    due_date_lt: int | None = Field(default=None, description="Due date before this unix-ms timestamp.")
    date_created_gt: int | None = Field(default=None, description="Created after this unix-ms timestamp.")
    date_created_lt: int | None = Field(default=None, description="Created before this unix-ms timestamp.")
    date_updated_gt: int | None = Field(default=None, description="Updated after this unix-ms timestamp.")
    date_updated_lt: int | None = Field(default=None, description="Updated before this unix-ms timestamp.")
    date_done_gt: int | None = Field(default=None, description="Completed after this unix-ms timestamp.")
    date_done_lt: int | None = Field(default=None, description="Completed before this unix-ms timestamp.")
    custom_items: list[int] | None = Field(default=None, description="Filter by custom task type ids.")
    custom_fields: list[dict[str, Any]] | None = Field(
        default=None,
        description="Custom-field filters, e.g. [{'field_id': '<uuid>', 'operator': '=', 'value': 'x'}].",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")

    def to_query(self) -> dict[str, Any]:
        query: dict[str, Any] = {"page": self.page}
        _put(query, "order_by", self.order_by)
        _put(query, "reverse", self.reverse)
        _put(query, "subtasks", self.subtasks)
        _put(query, "include_closed", self.include_closed)
        _put(query, "archived", self.archived)
        _put(query, "include_markdown_description", self.include_markdown_description)
        _put_list(query, "statuses[]", self.statuses)
        _put_list(query, "assignees[]", self.assignees)
        _put_list(query, "tags[]", self.tags)
        _put_list(query, "custom_items[]", self.custom_items)
        for key in (
            "due_date_gt",
            "due_date_lt",
            "date_created_gt",
            "date_created_lt",
            "date_updated_gt",
            "date_updated_lt",
            "date_done_gt",
            "date_done_lt",
        ):
            _put(query, key, getattr(self, key))
        if self.custom_fields is not None:
            query["custom_fields"] = _compact_json(self.custom_fields)
        return query


class UpdateTaskInput(_CustomTaskIdParams):
    """Path + query + body for updating a task."""

    task_id: str = Field(description="Task id (or Custom Task ID when custom_task_ids=true).")
    name: str | None = Field(default=None, min_length=1, description="New task name.")
    description: str | None = Field(default=None, description="New plain-text description.")
    markdown_content: str | None = Field(
        default=None, description="New markdown description; takes precedence over `description`."
    )
    status: str | None = Field(default=None, description="New status name.")
    priority: PriorityLevel | None = Field(default=None, description="1=Urgent, 2=High, 3=Normal, 4=Low.")
    due_date: int | None = Field(default=None, description="New due date (unix ms).")
    due_date_time: bool | None = Field(default=None, description="True if due_date carries a time component.")
    start_date: int | None = Field(default=None, description="New start date (unix ms).")
    start_date_time: bool | None = Field(default=None, description="True if start_date carries a time component.")
    time_estimate: int | None = Field(default=None, description="New time estimate (ms).")
    points: float | None = Field(default=None, description="New sprint points.")
    parent: str | None = Field(default=None, description="Move under this parent task id.")
    custom_item_id: int | None = Field(default=None, description="Custom task type id (0 = standard task).")
    archived: bool | None = Field(default=None, description="Archive (true) or restore (false) the task.")
    add_assignees: list[int] | None = Field(default=None, description="User ids to add as assignees.")
    remove_assignees: list[int] | None = Field(default=None, description="User ids to remove from assignees.")
    add_group_assignees: list[str] | None = Field(default=None, description="User-group ids to add.")
    remove_group_assignees: list[str] | None = Field(default=None, description="User-group ids to remove.")
    custom_fields: list[dict[str, Any]] | None = Field(
        default=None,
        description="Custom field values [{'id': '<uuid>', 'value': ...}]. For reliable per-field writes prefer "
        "clickup_set_custom_field_value.",
    )

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        _put(body, "name", self.name)
        _put(body, "description", self.description)
        _put(body, "markdown_content", self.markdown_content)
        _put(body, "status", self.status)
        _put(body, "priority", self.priority)
        _put(body, "due_date", self.due_date)
        _put(body, "due_date_time", self.due_date_time)
        _put(body, "start_date", self.start_date)
        _put(body, "start_date_time", self.start_date_time)
        _put(body, "time_estimate", self.time_estimate)
        _put(body, "points", self.points)
        _put(body, "parent", self.parent)
        _put(body, "custom_item_id", self.custom_item_id)
        _put(body, "archived", self.archived)
        _put_list(body, "custom_fields", self.custom_fields)

        assignees: dict[str, list[int]] = {}
        _put_list(assignees, "add", self.add_assignees)
        _put_list(assignees, "rem", self.remove_assignees)
        if assignees:
            body["assignees"] = assignees

        group_assignees: dict[str, list[str]] = {}
        _put_list(group_assignees, "add", self.add_group_assignees)
        _put_list(group_assignees, "rem", self.remove_group_assignees)
        if group_assignees:
            body["group_assignees"] = group_assignees
        return body


class DeleteTaskInput(_CustomTaskIdParams):
    """Path + query for deleting a task."""

    task_id: str = Field(description="Task id (or Custom Task ID when custom_task_ids=true).")


class GetFilteredTeamTasksInput(BaseModel):
    """Path (Workspace) + the full workspace-wide filter surface."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace id. Falls back to CLICKUP_TEAM_ID.")
    page: int = Field(default=0, ge=0, description="0-indexed page; up to 100 tasks per page.")
    order_by: OrderBy | None = Field(default=None, description="Sort field: id | created | updated | due_date.")
    reverse: bool | None = Field(default=None, description="Reverse the sort order.")
    subtasks: bool | None = Field(default=None, description="Include subtasks.")
    space_ids: list[str] | None = Field(default=None, description="Restrict to these Space ids.")
    project_ids: list[str] | None = Field(default=None, description="Restrict to these Folder ids.")
    list_ids: list[str] | None = Field(default=None, description="Restrict to these List ids.")
    statuses: list[str] | None = Field(default=None, description="Filter to these status names.")
    include_closed: bool | None = Field(default=None, description="Include tasks in closed statuses.")
    assignees: list[str] | None = Field(default=None, description="Filter by assignee user ids.")
    tags: list[str] | None = Field(default=None, description="Filter by tag names.")
    parent: str | None = Field(default=None, description="Only subtasks of this parent task id.")
    include_markdown_description: bool | None = Field(default=None, description="Return descriptions as markdown.")
    custom_task_ids: bool | None = Field(default=None, description="Return each task's Custom Task ID.")
    due_date_gt: int | None = Field(default=None, description="Due date after this unix-ms timestamp.")
    due_date_lt: int | None = Field(default=None, description="Due date before this unix-ms timestamp.")
    date_created_gt: int | None = Field(default=None, description="Created after this unix-ms timestamp.")
    date_created_lt: int | None = Field(default=None, description="Created before this unix-ms timestamp.")
    date_updated_gt: int | None = Field(default=None, description="Updated after this unix-ms timestamp.")
    date_updated_lt: int | None = Field(default=None, description="Updated before this unix-ms timestamp.")
    date_done_gt: int | None = Field(default=None, description="Completed after this unix-ms timestamp.")
    date_done_lt: int | None = Field(default=None, description="Completed before this unix-ms timestamp.")
    custom_items: list[int] | None = Field(default=None, description="Filter by custom task type ids.")
    custom_fields: list[dict[str, Any]] | None = Field(
        default=None, description="Custom-field filters (see clickup_get_tasks)."
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")

    def to_query(self) -> dict[str, Any]:
        query: dict[str, Any] = {"page": self.page}
        _put(query, "order_by", self.order_by)
        _put(query, "reverse", self.reverse)
        _put(query, "subtasks", self.subtasks)
        _put(query, "include_closed", self.include_closed)
        _put(query, "parent", self.parent)
        _put(query, "include_markdown_description", self.include_markdown_description)
        _put(query, "custom_task_ids", self.custom_task_ids)
        _put_list(query, "space_ids[]", self.space_ids)
        _put_list(query, "project_ids[]", self.project_ids)
        _put_list(query, "list_ids[]", self.list_ids)
        _put_list(query, "statuses[]", self.statuses)
        _put_list(query, "assignees[]", self.assignees)
        _put_list(query, "tags[]", self.tags)
        _put_list(query, "custom_items[]", self.custom_items)
        for key in (
            "due_date_gt",
            "due_date_lt",
            "date_created_gt",
            "date_created_lt",
            "date_updated_gt",
            "date_updated_lt",
            "date_done_gt",
            "date_done_lt",
        ):
            _put(query, key, getattr(self, key))
        if self.custom_fields is not None:
            query["custom_fields"] = _compact_json(self.custom_fields)
        return query


class MoveTaskInput(BaseModel):
    """Path + body for moving a task's home List (v3)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(description="Id of the task to move.")
    list_id: str = Field(description="Destination List id (the task's new home List).")
    workspace_id: str | None = Field(default=None, description="Workspace id. Falls back to CLICKUP_TEAM_ID.")
    move_custom_fields: bool | None = Field(
        default=None, description="Carry the source List's custom-field values across."
    )
    custom_fields_to_move: list[str] | None = Field(
        default=None, description="Specific custom-field ids to carry across."
    )
    status_mappings: list[dict[str, Any]] | None = Field(
        default=None,
        description="Map source statuses to destination statuses, e.g. [{'from': 'to do', 'to': 'open'}]. "
        "Required when the current status does not exist in the destination List.",
    )

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        _put(body, "move_custom_fields", self.move_custom_fields)
        _put_list(body, "custom_fields_to_move", self.custom_fields_to_move)
        _put_list(body, "status_mappings", self.status_mappings)
        return body


class MergeTasksInput(BaseModel):
    """Path + body for merging tasks into a target."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(description="Target task id that the others are merged into.")
    source_task_ids: list[str] = Field(
        min_length=1, description="Task ids to merge into the target (their content is folded in, then they close)."
    )


class CreateTaskFromTemplateInput(BaseModel):
    """Path + body for instantiating a task template."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(description="List to create the task in.")
    template_id: str = Field(description="Task template id.")
    name: str = Field(min_length=1, description="Name for the new task.")


class GetTaskTemplatesInput(BaseModel):
    """Path (Workspace) + paging for listing task templates."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace id. Falls back to CLICKUP_TEAM_ID.")
    page: int = Field(default=0, ge=0, description="0-indexed page (required by ClickUp).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")


class GetTaskTimeInStatusInput(_CustomTaskIdParams):
    """Path + query for one task's time-in-status."""

    task_id: str = Field(description="Task id (or Custom Task ID when custom_task_ids=true).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")


class GetBulkTasksTimeInStatusInput(_CustomTaskIdParams):
    """Query for time-in-status across many tasks at once."""

    task_ids: list[str] = Field(
        min_length=1, max_length=100, description="Task ids (up to 100) to fetch time-in-status for."
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="markdown (default) or json.")


# --------------------------------------------------------------------------- #
# Tools.
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="clickup_create_task",
    annotations=ToolAnnotations(
        title="Create Task",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_task(params: CreateTaskInput) -> str:
    """Create a task inside a List.

    Calls `POST /list/{list_id}/task`. Supports the full create surface: assignees
    and group assignees, tags, status, priority, due/start dates (unix ms), time
    estimate, sprint points, a markdown or plain description, custom-field values,
    subtask creation via `parent`, and a custom task type via `custom_item_id`.

    When to Use:
    - To add a new task (or a subtask, by setting `parent`) to a known List.

    When NOT to Use:
    - To change an existing task — use `clickup_update_task`.
    - To copy a predefined template — use `clickup_create_task_from_template`.

    Returns:
    A confirmation string with the new task's name, id, and URL.

    Examples:
    - Minimal: `params = {"list_id": "901", "name": "Draft spec"}`
    - Rich: `params = {"list_id": "901", "name": "Ship v2", "markdown_content":
      "## Goal\\n...", "assignees": [123], "priority": 2, "due_date": 1735689600000,
      "due_date_time": true, "tags": ["release"], "custom_fields": [{"id": "abc",
      "value": "done"}]}`

    Error Handling:
    400 usually means an unknown status/tag or a malformed custom_fields entry;
    404 means the List id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request("POST", f"/list/{params.list_id}/task", json_body=params.to_body())
        task = resp.json()
        return (
            f"Created task **{task.get('name')}** (id `{task.get('id')}`) in list {params.list_id}. "
            f"URL: {task.get('url', 'n/a')}"
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_task",
    annotations=ToolAnnotations(
        title="Get Task",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_task(params: GetTaskInput) -> str:
    """Fetch one task by id.

    Calls `GET /task/{task_id}`. Set `custom_task_ids=true` (plus `team_id` /
    CLICKUP_TEAM_ID) to look the task up by its Custom Task ID. Optionally pull
    subtasks and a markdown description.

    When to Use:
    - To read a single task's full detail when you already have its id.

    When NOT to Use:
    - To browse or search many tasks — use `clickup_get_tasks` (one List) or
      `clickup_get_filtered_team_tasks` (whole Workspace).

    Returns:
    Markdown detail (name, status, priority, assignees, dates, description) or the
    raw task JSON when `response_format="json"`.

    Examples:
    - `params = {"task_id": "86cxy1", "include_subtasks": true}`
    - By custom id: `params = {"task_id": "PROJ-42", "custom_task_ids": true,
      "team_id": "9000"}`

    Error Handling:
    404 means the id is wrong (or you passed a Custom Task ID without
    `custom_task_ids=true`).
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/task/{params.task_id}", params=params.query())
        return _format_single_task(resp.json(), params.response_format)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_tasks",
    annotations=ToolAnnotations(
        title="Get Tasks in List",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_tasks(params: GetTasksInput) -> str:
    """List tasks inside ONE List, with filters and sorting.

    Calls `GET /list/{list_id}/task`. Scope is a single List. Filter by statuses,
    assignees, tags, custom task types, and date ranges (created/updated/due/done,
    all unix ms), and sort with `order_by` + `reverse`.

    When to Use:
    - You know the List and want its tasks (a sprint board, a backlog column).

    When NOT to Use:
    - You need tasks across several Lists/Folders/Spaces at once, or you do not
      know which List they live in — use `clickup_get_filtered_team_tasks`, which
      searches the whole Workspace.
    - You already have a task id — use `clickup_get_task`.

    Returns:
    A paginated markdown summary (one bullet per task) or JSON. Only the first
    50 tasks of a page are rendered to stay under the size limit.

    Pagination:
    Page-based: pass `page` (0-indexed, up to 100 tasks per page). When the tool
    reports "More available", request the next page number.

    Examples:
    - `params = {"list_id": "901", "statuses": ["in progress"], "order_by":
      "due_date"}`
    - Next page: `params = {"list_id": "901", "page": 1}`

    Error Handling:
    404 means the List id is wrong; an empty result is a valid `page` past the end.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/list/{params.list_id}/task", params=params.to_query())
        body = resp.json()
        tasks = body.get("tasks", []) if isinstance(body, dict) else []
        last_page = body.get("last_page") if isinstance(body, dict) else None
        has_more = (not last_page) if last_page is not None else (len(tasks) >= _PAGE_SIZE)
        return _format_task_list(
            tasks,
            page=params.page,
            has_more=has_more,
            fmt=params.response_format,
            title=f"Tasks in list {params.list_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_task",
    annotations=ToolAnnotations(
        title="Update Task",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_task(params: UpdateTaskInput) -> str:
    """Update fields on an existing task.

    Calls `PUT /task/{task_id}`. Change the name, description (plain or markdown),
    status, priority, dates, time estimate, points, parent, or archived flag. Add
    or remove assignees and group assignees incrementally via the
    `add_assignees` / `remove_assignees` (and group equivalents) lists — ClickUp
    sends these as `{"assignees": {"add": [...], "rem": [...]}}`.

    When to Use:
    - To edit an existing task or (re)assign people.

    When NOT to Use:
    - To create a task — use `clickup_create_task`.
    - To move a task to a different List — use `clickup_move_task`.
    - For a single custom-field write, `clickup_set_custom_field_value` is more
      reliable than the `custom_fields` body here.

    Returns:
    A confirmation string naming the task and the fields that changed.

    Examples:
    - Reassign: `params = {"task_id": "86cxy1", "add_assignees": [123],
      "remove_assignees": [456]}`
    - Retitle + close: `params = {"task_id": "86cxy1", "name": "New title",
      "status": "complete"}`

    Error Handling:
    400 often means an unknown status name; 404 means the task id is wrong.
    """
    try:
        body = params.to_body()
        if not body:
            return "Error: no fields to update — supply at least one changed field."
        client = get_client()
        await client.request("PUT", f"/task/{params.task_id}", params=params.id_query(), json_body=body)
        return f"Updated task `{params.task_id}` — changed: {', '.join(sorted(body))}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_task",
    annotations=ToolAnnotations(
        title="Delete Task",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_task(params: DeleteTaskInput) -> str:
    """Permanently delete a task.

    Calls `DELETE /task/{task_id}`. This removes the task (and its subtasks); it
    cannot be undone via the API. Set `custom_task_ids=true` (+ `team_id`) to
    address the task by Custom Task ID.

    When to Use:
    - To remove a task you are certain should be gone.

    When NOT to Use:
    - To merely close or archive a task — use `clickup_update_task`
      (`status` / `archived`).

    Returns:
    A confirmation string.

    Examples:
    - `params = {"task_id": "86cxy1"}`

    Error Handling:
    404 means the task is already gone or the id is wrong.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/task/{params.task_id}", params=params.id_query())
        return f"Deleted task `{params.task_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_filtered_team_tasks",
    annotations=ToolAnnotations(
        title="Get Filtered Workspace Tasks",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_filtered_team_tasks(params: GetFilteredTeamTasksInput) -> str:
    """Search tasks across the ENTIRE Workspace with rich filters.

    Calls `GET /team/{team_id}/task`. Unlike `clickup_get_tasks` (one List), this
    spans every Space/Folder/List the token can see and narrows down with
    `space_ids`, `project_ids` (Folders), `list_ids`, statuses, assignees, tags,
    custom task types, and date ranges. `team_id` is the Workspace id and falls
    back to CLICKUP_TEAM_ID.

    When to Use:
    - "Find all my open tasks", "tasks due this week across projects", or any
      query where you do NOT know (or do not want to be limited to) one List.

    When NOT to Use:
    - You already know the single List — `clickup_get_tasks` is cheaper and its
      filters are List-scoped.
    - You have a task id — use `clickup_get_task`.

    Returns:
    A paginated markdown summary (one bullet per task) or JSON; first 50 tasks of
    the page rendered.

    Pagination:
    Page-based (`page`, 0-indexed, 100 per page). Request the next page number
    when "More available" is reported.

    Examples:
    - My open tasks: `params = {"assignees": ["123"], "include_closed": false}`
    - Due-window in two Spaces: `params = {"space_ids": ["10", "11"],
      "due_date_lt": 1735689600000, "order_by": "due_date"}`

    Error Handling:
    A missing `team_id` (and no CLICKUP_TEAM_ID) returns a clear error; 404 means
    the Workspace id is wrong.
    """
    try:
        team_id = _resolve_workspace_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/task", params=params.to_query())
        body = resp.json()
        tasks = body.get("tasks", []) if isinstance(body, dict) else []
        last_page = body.get("last_page") if isinstance(body, dict) else None
        has_more = (not last_page) if last_page is not None else (len(tasks) >= _PAGE_SIZE)
        return _format_task_list(
            tasks,
            page=params.page,
            has_more=has_more,
            fmt=params.response_format,
            title=f"Workspace tasks (team {team_id})",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_move_task",
    annotations=ToolAnnotations(
        title="Move Task to List",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_move_task(params: MoveTaskInput) -> str:
    """Move a task to a new home List.

    Calls the v3 endpoint
    `PUT /workspaces/{workspace_id}/tasks/{task_id}/home_list/{list_id}`. Because
    the destination List may use different statuses, pass `status_mappings` when
    the task's current status does not exist there. `move_custom_fields` /
    `custom_fields_to_move` control whether the source List's custom-field values
    travel with the task. `workspace_id` falls back to CLICKUP_TEAM_ID.

    When to Use:
    - To relocate a task from one List to another.

    When NOT to Use:
    - To keep a task in multiple Lists simultaneously (that is the tasks-in-
      multiple-lists ClickApp, handled by the Lists tools' add/remove).
    - To edit a task's fields in place — use `clickup_update_task`.

    Returns:
    A confirmation string.

    Examples:
    - `params = {"task_id": "86cxy1", "list_id": "902", "status_mappings":
      [{"from": "to do", "to": "open"}]}`

    Error Handling:
    400 typically means a status in the source List has no mapping in the target;
    404 means the task or List id is wrong.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        path = f"/workspaces/{workspace_id}/tasks/{params.task_id}/home_list/{params.list_id}"
        await client.request("PUT", path, json_body=params.to_body(), use_v3=True)
        return f"Moved task `{params.task_id}` to list {params.list_id}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_merge_tasks",
    annotations=ToolAnnotations(
        title="Merge Tasks",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_merge_tasks(params: MergeTasksInput) -> str:
    """Merge one or more source tasks into a target task.

    Calls `POST /task/{task_id}/merge` with `{"source_task_ids": [...]}`. The
    target keeps its id; each source task's content is folded in and the source is
    closed. Custom Task IDs are not supported here — use internal ids.

    When to Use:
    - To consolidate duplicate tasks into a single canonical task.

    When NOT to Use:
    - To move a task between Lists — use `clickup_move_task`.

    Returns:
    A confirmation string naming the target and merged source ids. (Destructive:
    the source tasks are consumed.)

    Examples:
    - `params = {"task_id": "86target", "source_task_ids": ["86dupA", "86dupB"]}`

    Error Handling:
    404 means the target or a source id is wrong.
    """
    try:
        client = get_client()
        await client.request(
            "POST", f"/task/{params.task_id}/merge", json_body={"source_task_ids": params.source_task_ids}
        )
        return f"Merged {', '.join(params.source_task_ids)} into task `{params.task_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_task_from_template",
    annotations=ToolAnnotations(
        title="Create Task from Template",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_task_from_template(params: CreateTaskFromTemplateInput) -> str:
    """Create a task in a List from a saved task template.

    Calls `POST /list/{list_id}/taskTemplate/{template_id}` with a `name`. Look up
    available template ids with `clickup_get_task_templates`.

    When to Use:
    - To spin up a task with a predefined structure (checklists, subtasks, fields).

    When NOT to Use:
    - For a plain new task — use `clickup_create_task`.

    Returns:
    A confirmation string with the new task's name and id.

    Examples:
    - `params = {"list_id": "901", "template_id": "t-123", "name": "Onboard Acme"}`

    Error Handling:
    404 means the List or template id is wrong.
    """
    try:
        client = get_client()
        resp = await client.request(
            "POST",
            f"/list/{params.list_id}/taskTemplate/{params.template_id}",
            json_body={"name": params.name},
        )
        task = resp.json() if resp.content else {}
        task_obj = task.get("task", task) if isinstance(task, dict) else {}
        return (
            f"Created task **{task_obj.get('name', params.name)}** "
            f"(id `{task_obj.get('id', 'n/a')}`) from template {params.template_id} in list {params.list_id}."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_task_templates",
    annotations=ToolAnnotations(
        title="Get Task Templates",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_task_templates(params: GetTaskTemplatesInput) -> str:
    """List the Workspace's saved task templates.

    Calls `GET /team/{team_id}/taskTemplate?page=N`. `team_id` falls back to
    CLICKUP_TEAM_ID. Use the returned ids with
    `clickup_create_task_from_template`.

    When to Use:
    - To discover template ids before creating a task from one.

    When NOT to Use:
    - For Folder/List templates — those live in the Folders/Lists tools.

    Returns:
    A markdown list of template names and ids, or JSON.

    Pagination:
    Page-based (`page`, 0-indexed, required by ClickUp).

    Examples:
    - `params = {"team_id": "9000", "page": 0}`

    Error Handling:
    A missing `team_id` (and no CLICKUP_TEAM_ID) returns a clear error.
    """
    try:
        team_id = _resolve_workspace_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/taskTemplate", params={"page": params.page})
        body = resp.json()
        templates = body.get("templates", []) if isinstance(body, dict) else []
        if params.response_format is ResponseFormat.JSON:
            return to_json({"page": params.page, "count": len(templates), "templates": templates})
        lines = [
            f"# Task templates (team {team_id})",
            "",
            f"Page **{params.page}** — **{len(templates)}** template(s).",
        ]
        lines.append("")
        for tpl in templates:
            lines.append(f"- **{tpl.get('name', '(unnamed)')}** (`{tpl.get('id', '?')}`)")
        if not templates:
            lines.append("_No templates._")
        return "\n".join(lines)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_task_time_in_status",
    annotations=ToolAnnotations(
        title="Get Task Time in Status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_task_time_in_status(params: GetTaskTimeInStatusInput) -> str:
    """Report how long one task has spent in each status.

    Calls `GET /task/{task_id}/time_in_status`. Requires the 'Total time in
    Status' ClickApp to be enabled. Set `custom_task_ids=true` (+ `team_id`) to
    address the task by Custom Task ID.

    When to Use:
    - Cycle-time / workflow analysis for a single task.

    When NOT to Use:
    - For many tasks at once — use `clickup_get_bulk_tasks_time_in_status`.

    Returns:
    Markdown (current status + per-status history in `Xh Ym`) or raw JSON.

    Examples:
    - `params = {"task_id": "86cxy1"}`

    Error Handling:
    An empty result usually means the ClickApp is disabled; 404 means a bad id.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/task/{params.task_id}/time_in_status", params=params.id_query())
        data = resp.json()
        if params.response_format is ResponseFormat.JSON:
            return to_json(data)
        return _time_in_status_markdown(data, f"Time in status — task {params.task_id}")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_bulk_tasks_time_in_status",
    annotations=ToolAnnotations(
        title="Get Bulk Tasks Time in Status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_bulk_tasks_time_in_status(params: GetBulkTasksTimeInStatusInput) -> str:
    """Report time-in-status for up to 100 tasks in one call.

    Calls `GET /task/bulk_time_in_status/task_ids` with each id passed as a
    repeated `task_ids` query param. Requires the 'Total time in Status' ClickApp.
    Set `custom_task_ids=true` (+ `team_id`) to address tasks by Custom Task ID.

    When to Use:
    - Cycle-time analysis across a batch of tasks.

    When NOT to Use:
    - For a single task, `clickup_get_task_time_in_status` is simpler.

    Returns:
    Markdown grouped per task (current status + history), or raw JSON keyed by
    task id.

    Examples:
    - `params = {"task_ids": ["86a", "86b", "86c"]}`

    Error Handling:
    More than 100 ids is rejected client-side; empty results mean the ClickApp is
    disabled.
    """
    try:
        client = get_client()
        query = params.id_query()
        query["task_ids"] = params.task_ids
        resp = await client.request("GET", "/task/bulk_time_in_status/task_ids", params=query)
        data = resp.json()
        if params.response_format is ResponseFormat.JSON:
            return to_json(data)
        if not isinstance(data, dict) or not data:
            return "# Bulk time in status\n\n_No time-in-status data (enable the 'Total time in Status' ClickApp)._"
        sections = ["# Bulk time in status", ""]
        for task_id, entry in data.items():
            sections.append(_time_in_status_markdown(entry or {}, f"Task {task_id}"))
            sections.append("")
        return "\n".join(sections).rstrip()
    except Exception as exc:
        return handle_api_error(exc)
