"""Time Tracking 2.0 tools — time entries CRUD/start-stop, entry tags, and
per-assignee time estimates.

Legacy Time Tracking v1 endpoints (`/task/{task_id}/time`) are intentionally
NOT implemented here — an ADR reversibility trigger skips v1 because Time
Tracking 2.0 (`/team/{team_id}/time_entries`) fully supersedes it with richer
filtering, tags, and approval fields; there is no unique v1 capability worth
maintaining two parallel time-tracking surfaces for.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

import httpx
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Context-window guard: independent of any API-side paging.
MAX_DISPLAY_ROWS = 50


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _resolve_team_id(team_id: str | None) -> str:
    """Fall back to CLICKUP_TEAM_ID when the caller omits team_id."""
    resolved = team_id or get_settings().clickup_team_id
    if not resolved:
        raise RuntimeError("team_id is required: pass it explicitly or set CLICKUP_TEAM_ID in the environment/.env.")
    return resolved


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """Drop `None` values so they never reach the querystring/body."""
    return {k: v for k, v in data.items() if v is not None}


def _custom_id_params(custom_task_ids: bool, team_id: str) -> dict[str, Any]:
    """ClickUp's custom-task-id pattern: pair `custom_task_ids=true` with a query `team_id`."""
    return {"custom_task_ids": True, "team_id": team_id} if custom_task_ids else {}


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    """Defensively parse a response body — some mutation endpoints return none."""
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a `{"data": [...]}` (or bare-list/bare-dict) response into a list."""
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and data:
        return [data]
    return []


def _extract_single(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a `{"data": {...}}` (or one-element list) response into one item."""
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict) and data:
        return data
    return None


def _human_delta(ms: int) -> str:
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_duration(duration: Any) -> str:
    """Render a duration in ms; negative means an actively running timer."""
    try:
        ms = int(duration)
    except (TypeError, ValueError):
        return str(duration)
    if ms < 0:
        return f"RUNNING (elapsed {_human_delta(-ms)} so far)"
    return _human_delta(ms)


def _format_time_entry(entry: dict[str, Any]) -> str:
    entry_id = entry.get("id", "unknown")
    task = entry.get("task") or {}
    task_desc = f"{task.get('name')} ({task.get('id')})" if task else "no task"
    user = entry.get("user") or {}
    username = user.get("username", "unknown")
    start = epoch_to_human(entry.get("start"))
    end_raw = entry.get("end")
    end = epoch_to_human(end_raw) if end_raw else "—"
    duration = _format_duration(entry.get("duration", 0))
    billable = "billable" if entry.get("billable") else "non-billable"
    tags = entry.get("tags") or []
    tag_names = ", ".join(t.get("name", "") for t in tags) if tags else "none"
    return (
        f"- **{entry_id}** — {task_desc} — {username} — {start} → {end} — {duration} ({billable}) — tags: {tag_names}"
    )


def _format_tag(tag: dict[str, Any]) -> str:
    name = tag.get("name", "unknown")
    fg = tag.get("tag_fg") or "n/a"
    bg = tag.get("tag_bg") or "n/a"
    creator = tag.get("creator", "unknown")
    return f"- **{name}** (fg {fg} / bg {bg}) — created by user {creator}"


def _format_generic(item: dict[str, Any]) -> str:
    parts = ", ".join(f"{k}={v}" for k, v in item.items())
    return f"- {parts}"


def _format_estimates(data: dict[str, Any]) -> str:
    total = data.get("total_time_estimate")
    total_str = _human_delta(int(total)) if total is not None else "unknown"
    breakdown_key = "assignee_estimates" if "assignee_estimates" in data else "updated_estimates"
    breakdown = data.get(breakdown_key) or {}
    lines = [f"Total estimate: **{total_str}**."]
    for assignee, ms in breakdown.items():
        lines.append(f"- user {assignee}: {_human_delta(int(ms))}")
    return "\n".join(lines)


def _render_list(
    *,
    items: list[dict[str, Any]],
    fmt: ResponseFormat,
    title: str,
    formatter: Callable[[dict[str, Any]], str],
) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "count": len(items), "items": items})
    plural = "y" if len(items) == 1 else "ies"
    lines = [f"# {title}", "", f"Found **{len(items)}** entr{plural}.", ""]
    shown = items[:MAX_DISPLAY_ROWS]
    for item in shown:
        lines.append(formatter(item))
    if not items:
        lines.append("_No items._")
    if len(items) > MAX_DISPLAY_ROWS:
        lines.append("")
        lines.append(f"_Showing first {MAX_DISPLAY_ROWS} of {len(items)}; narrow your filters for the rest._")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Shared sub-models
# --------------------------------------------------------------------------


class TimeEntryTagInput(BaseModel):
    """A time-entry tag: label plus optional display colors."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, description="Tag label.")
    tag_fg: str | None = Field(default=None, description="Foreground/text hex color, e.g. '#FFFFFF'.")
    tag_bg: str | None = Field(default=None, description="Background hex color, e.g. '#BF55EC'.")

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name}
        if self.tag_fg is not None:
            body["tag_fg"] = self.tag_fg
        if self.tag_bg is not None:
            body["tag_bg"] = self.tag_bg
        return body


class TimeEstimateInput(BaseModel):
    """One per-assignee time estimate entry."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    assignee: int | Literal["unassigned"] = Field(
        description="Assignee user id, or the literal 'unassigned' for work not yet assigned to anyone."
    )
    time: int = Field(ge=0, description="Time estimate in milliseconds (non-negative).")

    def to_body(self) -> dict[str, Any]:
        return {"assignee": self.assignee, "time": self.time}


# --------------------------------------------------------------------------
# clickup_get_time_entries
# --------------------------------------------------------------------------


class GetTimeEntriesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    start_date: int | None = Field(default=None, description="Unix ms lower bound (inclusive).")
    end_date: int | None = Field(default=None, description="Unix ms upper bound (inclusive).")
    assignee: str | None = Field(
        default=None,
        description="Filter by user id(s); comma-separated for multiple (e.g. '1234,9876'). "
        "Workspace Owners/Admins only.",
    )
    include_task_tags: bool = Field(default=False, description="Include each entry's associated task tags.")
    include_location_names: bool = Field(
        default=False, description="Include List/Folder/Space names alongside their ids."
    )
    include_approval_history: bool = Field(
        default=False, description="Include approval status changes, notes, and approver details."
    )
    include_approval_details: bool = Field(
        default=False, description="Include approver id, approval time, approver list, and status."
    )
    space_id: str | None = Field(default=None, description="Limit to entries for tasks in this Space.")
    folder_id: str | None = Field(default=None, description="Limit to entries for tasks in this Folder.")
    list_id: str | None = Field(default=None, description="Limit to entries for tasks in this List.")
    task_id: str | None = Field(default=None, description="Limit to entries for this task.")
    custom_task_ids: bool = Field(
        default=False, description="Set True when `task_id` is a custom task id (requires team_id)."
    )
    is_billable: bool | None = Field(default=None, description="Filter by billable status.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


@mcp.tool(
    name="clickup_get_time_entries",
    annotations=ToolAnnotations(
        title="Get Time Entries Within a Date Range",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_time_entries(params: GetTimeEntriesInput) -> str:
    """List time entries in a Workspace, optionally scoped by date range and location.

    Calls `GET /team/{team_id}/time_entries`, ClickUp's Time Tracking 2.0
    surface. Without `start_date`/`end_date` ClickUp returns every entry in
    the Workspace, so narrow the range and/or scope (space/folder/list/task)
    on busy Workspaces.

    When to Use:
    - Auditing or summarizing time logged across a Workspace, Space, Folder,
      List, or single task over a period.
    - Building a timesheet/report (combine with `is_billable` and `assignee`).

    When NOT to Use:
    - To fetch one known entry by id — use `clickup_get_time_entry`.
    - To check only the entry currently running for a user — use
      `clickup_get_running_time_entry` (cheaper, no date math needed).

    Returns:
    A markdown (default) or JSON list of entries — id, task, user, start/end,
    duration, billable flag, and tags. A **negative `duration`** means that
    entry is an actively running timer for that user (elapsed-so-far), not a
    finished duration.

    Pagination:
    This endpoint has no `limit`/`offset` — ClickUp returns the full matching
    set in one call. Display here is capped at 50 rows for context-window
    safety; narrow `start_date`/`end_date` or the location filters to see
    everything without truncation.

    Examples:
    params = {"team_id": "123", "start_date": 1700000000000, "end_date": 1700600000000, "is_billable": True}
    params = {"team_id": "123", "task_id": "abc123", "include_task_tags": True}

    Error Handling:
    404 means team_id/space_id/folder_id/list_id/task_id doesn't exist or
    isn't accessible; 429 means the ~100 req/min per-token rate limit was hit.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        query = _compact(
            {
                "start_date": params.start_date,
                "end_date": params.end_date,
                "assignee": params.assignee,
                "include_task_tags": params.include_task_tags or None,
                "include_location_names": params.include_location_names or None,
                "include_approval_history": params.include_approval_history or None,
                "include_approval_details": params.include_approval_details or None,
                "space_id": params.space_id,
                "folder_id": params.folder_id,
                "list_id": params.list_id,
                "task_id": params.task_id,
                "is_billable": params.is_billable,
            }
        )
        query.update(_custom_id_params(params.custom_task_ids, team_id))
        resp = await client.request("GET", f"/team/{team_id}/time_entries", params=query)
        entries = _extract_list(_safe_json(resp))
        return _render_list(
            items=entries, fmt=params.response_format, title="Time Entries", formatter=_format_time_entry
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_get_time_entry
# --------------------------------------------------------------------------


class GetTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    time_entry_id: str = Field(min_length=1, description="The time entry id to fetch.")
    include_task_tags: bool = Field(default=False, description="Include the associated task's tags.")
    include_location_names: bool = Field(
        default=False, description="Include List/Folder/Space names alongside their ids."
    )
    include_approval_history: bool = Field(default=False, description="Include the approval history.")
    include_approval_details: bool = Field(default=False, description="Include the approval details.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


@mcp.tool(
    name="clickup_get_time_entry",
    annotations=ToolAnnotations(
        title="Get Singular Time Entry",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_time_entry(params: GetTimeEntryInput) -> str:
    """Fetch one time entry by id.

    Calls `GET /team/{team_id}/time_entries/{time_entry_id}`.

    When to Use:
    - You already have a `time_entry_id` (from `clickup_get_time_entries`, or
      a create/start response) and want its current full detail.

    When NOT to Use:
    - To search/list entries — use `clickup_get_time_entries`.
    - To see prior edits to this entry — use `clickup_get_time_entry_history`.
    - To check the currently-running timer without an id — use
      `clickup_get_running_time_entry`.

    Returns:
    A markdown (default) or JSON summary of the entry. A **negative
    `duration`** means the timer is still running for that user.

    Examples:
    params = {"team_id": "123", "time_entry_id": "1963465985517105840", "include_location_names": True}

    Error Handling:
    404 means the time_entry_id doesn't exist, or exists under a different team_id.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        query = _compact(
            {
                "include_task_tags": params.include_task_tags or None,
                "include_location_names": params.include_location_names or None,
                "include_approval_history": params.include_approval_history or None,
                "include_approval_details": params.include_approval_details or None,
            }
        )
        resp = await client.request("GET", f"/team/{team_id}/time_entries/{params.time_entry_id}", params=query)
        entry = _extract_single(_safe_json(resp))
        if entry is None:
            return f"No time entry found for id {params.time_entry_id}."
        if params.response_format is ResponseFormat.JSON:
            return to_json(entry)
        return f"# Time Entry {params.time_entry_id}\n\n{_format_time_entry(entry)}"
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_get_time_entry_history
# --------------------------------------------------------------------------


class GetTimeEntryHistoryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    time_entry_id: str = Field(min_length=1, description="The time entry id whose change history to fetch.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


@mcp.tool(
    name="clickup_get_time_entry_history",
    annotations=ToolAnnotations(
        title="Get Time Entry History",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_time_entry_history(params: GetTimeEntryHistoryInput) -> str:
    """View the list of changes made to a time entry.

    Calls `GET /team/{team_id}/time_entries/{time_entry_id}/history`.

    When to Use:
    - Auditing who changed a time entry's duration/description/task
      association and when.

    When NOT to Use:
    - To read the entry's current state — use `clickup_get_time_entry`.

    Returns:
    A markdown (default) or JSON list of raw change records as ClickUp
    returns them (field names vary by change type; this endpoint's payload
    shape isn't fully documented upstream, so entries render as key=value
    pairs rather than a fixed schema).

    Examples:
    params = {"team_id": "123", "time_entry_id": "1963465985517105840"}

    Error Handling:
    404 means the time_entry_id doesn't exist or isn't accessible.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/time_entries/{params.time_entry_id}/history")
        history = _extract_list(_safe_json(resp))
        return _render_list(
            items=history,
            fmt=params.response_format,
            title=f"History — Time Entry {params.time_entry_id}",
            formatter=_format_generic,
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_get_running_time_entry
# --------------------------------------------------------------------------


class GetRunningTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    assignee: int | None = Field(default=None, description="User id to check; defaults to the token's own user.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


@mcp.tool(
    name="clickup_get_running_time_entry",
    annotations=ToolAnnotations(
        title="Get Running Time Entry",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_running_time_entry(params: GetRunningTimeEntryInput) -> str:
    """Get the currently running time entry for a user, if any.

    Calls `GET /team/{team_id}/time_entries/current`.

    When to Use:
    - Checking whether a timer is active right now before starting a new one
      (`clickup_start_time_entry`) or stopping one (`clickup_stop_time_entry`).

    When NOT to Use:
    - To browse historical entries — use `clickup_get_time_entries`.

    Returns:
    A markdown (default) or JSON summary of the running entry, or a plain
    "No timer is currently running" message. When present, its `duration` is
    **negative** — that is how ClickUp signals a still-running timer (the
    magnitude is the elapsed time so far).

    Examples:
    params = {"team_id": "123"}
    params = {"team_id": "123", "assignee": 300528}

    Error Handling:
    403 means you lack permission to view another user's running timer.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        query = _compact({"assignee": params.assignee})
        resp = await client.request("GET", f"/team/{team_id}/time_entries/current", params=query)
        entry = _extract_single(_safe_json(resp))
        if entry is None:
            return "No timer is currently running for the requested user."
        if params.response_format is ResponseFormat.JSON:
            return to_json(entry)
        return f"# Running Time Entry\n\n{_format_time_entry(entry)}"
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_create_time_entry
# --------------------------------------------------------------------------


class CreateTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    start: int = Field(description="Start timestamp, unix ms.")
    duration: int | None = Field(default=None, description="Duration in ms. Provide this or `stop` (not both).")
    stop: int | None = Field(default=None, description="Stop timestamp, unix ms. Provide this or `duration`.")
    description: str | None = Field(default=None, description="Time entry description.")
    tags: list[TimeEntryTagInput] | None = Field(
        default=None, description="Tags to attach (unlimited on Business Plus/Enterprise, capped otherwise)."
    )
    billable: bool | None = Field(default=None, description="Whether this entry is billable.")
    assignee: int | None = Field(
        default=None,
        description="User id to log the entry against. Workspace Owners/Admins may assign to any user; "
        "Members are limited to themselves.",
    )
    tid: str | None = Field(default=None, description="Task id to associate the entry with (optional).")
    custom_task_ids: bool = Field(
        default=False, description="Set True when `tid` is a custom task id (requires team_id)."
    )

    @model_validator(mode="after")
    def _check_duration_or_stop(self) -> CreateTimeEntryInput:
        if self.duration is None and self.stop is None:
            raise ValueError("Provide either `duration` (ms) or `stop` (unix ms timestamp).")
        return self


@mcp.tool(
    name="clickup_create_time_entry",
    annotations=ToolAnnotations(
        title="Create a Time Entry",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_time_entry(params: CreateTimeEntryInput) -> str:
    """Log a completed (already-finished) time entry.

    Calls `POST /team/{team_id}/time_entries`.

    When to Use:
    - Backfilling time that was tracked elsewhere, or logging a block of
      work with a known start and either an end time or a duration.

    When NOT to Use:
    - To start an open-ended running timer right now — use
      `clickup_start_time_entry` instead (no `duration`/`stop` needed there).

    Returns:
    A confirmation string with the created entry's id, task, user, and
    duration.

    Examples:
    params = {"team_id": "123", "start": 1700000000000, "duration": 3600000, "tid": "abc123", "billable": True}
    params = {"team_id": "123", "start": 1700000000000, "stop": 1700003600000, "description": "code review"}

    Error Handling:
    400 means the payload is malformed (e.g. neither `duration` nor `stop`
    given, or a bad `start`); 404 means `tid` doesn't reference a real task.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        body = _compact(
            {
                "description": params.description,
                "tags": [t.to_body() for t in params.tags] if params.tags else None,
                "start": params.start,
                "stop": params.stop,
                "duration": params.duration,
                "billable": params.billable,
                "assignee": params.assignee,
                "tid": params.tid,
            }
        )
        query = _custom_id_params(params.custom_task_ids, team_id)
        resp = await client.request("POST", f"/team/{team_id}/time_entries", json_body=body, params=query or None)
        entry = _extract_single(_safe_json(resp))
        if entry:
            return f"Created time entry.\n\n{_format_time_entry(entry)}"
        return "Created time entry."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_update_time_entry
# --------------------------------------------------------------------------


class UpdateTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    time_entry_id: str = Field(min_length=1, description="The time entry id to update.")
    description: str | None = Field(default=None, description="New description.")
    tags: list[TimeEntryTagInput] | None = Field(
        default=None, description="Tags to apply — combine with `tag_action` to say how."
    )
    tag_action: Literal["replace", "add", "remove"] | None = Field(
        default=None,
        description="How to apply `tags`: 'replace' overwrites all tags, 'add' unions them in, 'remove' "
        "removes them. Exactly one tag action is allowed per request.",
    )
    start: int | None = Field(default=None, description="New start timestamp, unix ms. Requires `end` too.")
    end: int | None = Field(default=None, description="New end timestamp, unix ms. Requires `start` too.")
    tid: str | None = Field(default=None, description="Task id to (re-)associate the entry with.")
    billable: bool | None = Field(default=None, description="Whether this entry is billable.")
    duration: int | None = Field(default=None, description="New duration in ms.")
    custom_task_ids: bool = Field(
        default=False, description="Set True when `tid` is a custom task id (requires team_id)."
    )

    @model_validator(mode="after")
    def _check_start_end_pair(self) -> UpdateTimeEntryInput:
        if (self.start is None) != (self.end is None):
            raise ValueError("`start` and `end` must be provided together.")
        return self


@mcp.tool(
    name="clickup_update_time_entry",
    annotations=ToolAnnotations(
        title="Update a Time Entry",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_time_entry(params: UpdateTimeEntryInput) -> str:
    """Edit an existing time entry's description, tags, times, task, or billable flag.

    Calls `PUT /team/{team_id}/time_entries/{time_entry_id}`.

    When to Use:
    - Correcting a logged entry's start/end, re-tagging it, or reassociating
      it with a different task.

    When NOT to Use:
    - To manage tags across the whole Workspace (rename/remove the tag
      itself) — use `clickup_rename_time_entry_tag` /
      `clickup_remove_time_entry_tags` instead; this tool only changes which
      tags are on *this* entry.
    - To stop a running timer — use `clickup_stop_time_entry`.

    Returns:
    A confirmation string, including the updated entry's detail when the API
    returns one.

    Examples:
    params = {"team_id": "123", "time_entry_id": "abc", "tags": [{"name": "billing"}], "tag_action": "add"}
    params = {"team_id": "123", "time_entry_id": "abc", "start": 1700000000000, "end": 1700003600000}

    Error Handling:
    400 means `start`/`end` were given without their pair, or more than one
    tag action was implied; 404 means the time_entry_id doesn't exist.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        body = _compact(
            {
                "description": params.description,
                "tags": [t.to_body() for t in params.tags] if params.tags else None,
                "tag_action": params.tag_action,
                "start": params.start,
                "end": params.end,
                "tid": params.tid,
                "billable": params.billable,
                "duration": params.duration,
            }
        )
        query = _custom_id_params(params.custom_task_ids, team_id)
        resp = await client.request(
            "PUT",
            f"/team/{team_id}/time_entries/{params.time_entry_id}",
            json_body=body,
            params=query or None,
        )
        entry = _extract_single(_safe_json(resp))
        if entry:
            return f"Updated time entry {params.time_entry_id}.\n\n{_format_time_entry(entry)}"
        return f"Updated time entry {params.time_entry_id}."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_delete_time_entry
# --------------------------------------------------------------------------


class DeleteTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    time_entry_id: str = Field(
        min_length=1, description="The time entry id to delete. Comma-separate multiple ids to bulk-delete."
    )


@mcp.tool(
    name="clickup_delete_time_entry",
    annotations=ToolAnnotations(
        title="Delete a Time Entry",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_time_entry(params: DeleteTimeEntryInput) -> str:
    """Permanently delete one or more time entries.

    Calls `DELETE /team/{team_id}/time_entries/{time_entry_id}`.

    When to Use:
    - Removing a duplicate, mistaken, or test time entry.

    When NOT to Use:
    - To stop (but keep) a running timer — use `clickup_stop_time_entry`.

    Returns:
    A confirmation string naming the deleted id(s). This is irreversible.

    Examples:
    params = {"team_id": "123", "time_entry_id": "1963465985517105840"}
    params = {"team_id": "123", "time_entry_id": "111,222,333"}

    Error Handling:
    404 means the time_entry_id(s) don't exist or aren't accessible.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        await client.request("DELETE", f"/team/{team_id}/time_entries/{params.time_entry_id}")
        return f"Deleted time entry id(s): {params.time_entry_id}."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_start_time_entry
# --------------------------------------------------------------------------


class StartTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    description: str | None = Field(default=None, description="Timer description.")
    tags: list[TimeEntryTagInput] | None = Field(default=None, description="Tags to attach to the running timer.")
    tid: str | None = Field(
        default=None,
        description="Task id to associate the entry with. Unlimited unassociated entries are available on "
        "Business Plus/Enterprise.",
    )
    billable: bool | None = Field(default=None, description="Whether this entry is billable.")
    custom_task_ids: bool = Field(
        default=False, description="Set True when `tid` is a custom task id (requires team_id)."
    )


@mcp.tool(
    name="clickup_start_time_entry",
    annotations=ToolAnnotations(
        title="Start a Time Entry",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_start_time_entry(params: StartTimeEntryInput) -> str:
    """Start a new, open-ended running timer for the authenticated user.

    Calls `POST /team/{team_id}/time_entries/start`.

    When to Use:
    - Beginning live time tracking on a task right now, with no known end
      time yet.

    When NOT to Use:
    - To log time you already know the duration/end for — use
      `clickup_create_time_entry` instead.
    - If a timer may already be running — check first with
      `clickup_get_running_time_entry` (ClickUp allows only one active timer
      per user).

    Returns:
    A confirmation string with the new entry's id. Its `duration` in the API
    response is **negative** while it runs — that is expected, not an error.

    Examples:
    params = {"team_id": "123", "tid": "abc123", "description": "pairing session"}

    Error Handling:
    400 typically means a timer is already running for this user; stop it
    first with `clickup_stop_time_entry`.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        body = _compact(
            {
                "description": params.description,
                "tags": [t.to_body() for t in params.tags] if params.tags else None,
                "tid": params.tid,
                "billable": params.billable,
            }
        )
        query = _custom_id_params(params.custom_task_ids, team_id)
        resp = await client.request("POST", f"/team/{team_id}/time_entries/start", json_body=body, params=query or None)
        entry = _extract_single(_safe_json(resp))
        if entry:
            return f"Started timer.\n\n{_format_time_entry(entry)}"
        return "Started timer."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_stop_time_entry
# --------------------------------------------------------------------------


class StopTimeEntryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")


@mcp.tool(
    name="clickup_stop_time_entry",
    annotations=ToolAnnotations(
        title="Stop a Time Entry",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_stop_time_entry(params: StopTimeEntryInput) -> str:
    """Stop the authenticated user's currently running timer.

    Calls `POST /team/{team_id}/time_entries/stop`.

    When to Use:
    - Ending live time tracking started via `clickup_start_time_entry`.

    When NOT to Use:
    - To delete the entry outright — use `clickup_delete_time_entry` after
      stopping it, if that's the intent.

    Returns:
    A confirmation string with the now-finished entry's detail (its
    `duration` becomes positive once stopped), or a plain message if nothing
    was running.

    Examples:
    params = {"team_id": "123"}

    Error Handling:
    400 typically means no timer was running for this user.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("POST", f"/team/{team_id}/time_entries/stop")
        entry = _extract_single(_safe_json(resp))
        if entry:
            return f"Stopped timer.\n\n{_format_time_entry(entry)}"
        return "Stopped timer."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_get_time_entry_tags
# --------------------------------------------------------------------------


class GetTimeEntryTagsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


@mcp.tool(
    name="clickup_get_time_entry_tags",
    annotations=ToolAnnotations(
        title="Get All Tags from Time Entries",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_time_entry_tags(params: GetTimeEntryTagsInput) -> str:
    """List every tag ever applied to a time entry in this Workspace.

    Calls `GET /team/{team_id}/time_entries/tags`.

    When to Use:
    - Discovering existing tag names/colors before tagging more entries, or
      before renaming one with `clickup_rename_time_entry_tag`.

    When NOT to Use:
    - To see the tags on one specific entry — read them off
      `clickup_get_time_entry` / `clickup_get_time_entries` instead.

    Returns:
    A markdown (default) or JSON list of tags with name, colors, and creator.

    Examples:
    params = {"team_id": "123"}

    Error Handling:
    404 means the team_id doesn't exist or isn't accessible.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/time_entries/tags")
        tags = _extract_list(_safe_json(resp))
        return _render_list(items=tags, fmt=params.response_format, title="Time Entry Tags", formatter=_format_tag)
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_add_time_entry_tags
# --------------------------------------------------------------------------


class AddTimeEntryTagsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    time_entry_ids: list[str] = Field(min_length=1, description="Time entry ids to tag.")
    tags: list[TimeEntryTagInput] = Field(min_length=1, description="Tags to apply to each listed entry.")


@mcp.tool(
    name="clickup_add_time_entry_tags",
    annotations=ToolAnnotations(
        title="Add Tags to Time Entries",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_add_time_entry_tags(params: AddTimeEntryTagsInput) -> str:
    """Apply one or more tags to one or more time entries.

    Calls `POST /team/{team_id}/time_entries/tags`. This is one of the three
    dedicated tag actions on this resource — the others are
    `clickup_remove_time_entry_tags` (remove) and `clickup_rename_time_entry_tag`
    (rename a tag everywhere); each call performs exactly one such action.

    When to Use:
    - Bulk-tagging several time entries at once, or adding a brand-new tag
      (it is created implicitly on first use).

    When NOT to Use:
    - To change tags on a single entry inline while editing it — use
      `clickup_update_time_entry` with `tag_action="add"` instead.

    Returns:
    A confirmation string naming the tag(s) and how many entries were tagged.

    Examples:
    params = {"team_id": "123", "time_entry_ids": ["abc", "def"], "tags": [{"name": "billing", "tag_bg": "#BF55EC"}]}

    Error Handling:
    404 means one of the time_entry_ids doesn't exist or isn't accessible.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        body = {
            "time_entry_ids": params.time_entry_ids,
            "tags": [t.to_body() for t in params.tags],
        }
        await client.request("POST", f"/team/{team_id}/time_entries/tags", json_body=body)
        tag_names = ", ".join(t.name for t in params.tags)
        plural = "y" if len(params.time_entry_ids) == 1 else "ies"
        return f"Added tag(s) [{tag_names}] to {len(params.time_entry_ids)} time entr{plural}."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_remove_time_entry_tags
# --------------------------------------------------------------------------


class RemoveTimeEntryTagsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    time_entry_ids: list[str] = Field(min_length=1, description="Time entry ids to untag.")
    tags: list[TimeEntryTagInput] = Field(min_length=1, description="Tags to remove from each listed entry.")


@mcp.tool(
    name="clickup_remove_time_entry_tags",
    annotations=ToolAnnotations(
        title="Remove Tags from Time Entries",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_time_entry_tags(params: RemoveTimeEntryTagsInput) -> str:
    """Remove one or more tags from one or more time entries.

    Calls `DELETE /team/{team_id}/time_entries/tags`. This does NOT delete
    the tag from the Workspace — only its association with these entries;
    the tag remains available for `clickup_add_time_entry_tags` afterward.

    When to Use:
    - Untagging a batch of entries without touching the tag definition
      itself.

    When NOT to Use:
    - To delete the tag everywhere (rename it out of existence) — there is
      no dedicated "delete tag" endpoint; use `clickup_rename_time_entry_tag`
      if you need to repurpose it instead.

    Returns:
    A confirmation string naming the tag(s) and how many entries were
    untagged.

    Examples:
    params = {"team_id": "123", "time_entry_ids": ["abc", "def"], "tags": [{"name": "billing"}]}

    Error Handling:
    404 means one of the time_entry_ids doesn't exist or isn't accessible.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        body = {
            "time_entry_ids": params.time_entry_ids,
            "tags": [t.to_body() for t in params.tags],
        }
        await client.request("DELETE", f"/team/{team_id}/time_entries/tags", json_body=body)
        tag_names = ", ".join(t.name for t in params.tags)
        plural = "y" if len(params.time_entry_ids) == 1 else "ies"
        return (
            f"Removed tag(s) [{tag_names}] from {len(params.time_entry_ids)} time entr{plural} "
            "(the tag(s) still exist in the Workspace)."
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_rename_time_entry_tag
# --------------------------------------------------------------------------


class RenameTimeEntryTagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace (Team) id. Defaults to CLICKUP_TEAM_ID.")
    name: str = Field(min_length=1, description="The tag's current name.")
    new_name: str = Field(min_length=1, description="The tag's new name.")
    tag_bg: str = Field(min_length=1, description="Background hex color to set, e.g. '#BF55EC'.")
    tag_fg: str = Field(min_length=1, description="Foreground/text hex color to set, e.g. '#FFFFFF'.")


@mcp.tool(
    name="clickup_rename_time_entry_tag",
    annotations=ToolAnnotations(
        title="Change Tag Names from Time Entries",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_rename_time_entry_tag(params: RenameTimeEntryTagInput) -> str:
    """Rename a time-entry tag (and set its colors) across the whole Workspace.

    Calls `PUT /team/{team_id}/time_entries/tags`. Unlike
    `clickup_add_time_entry_tags`/`clickup_remove_time_entry_tags`, which act
    on specific entries, this changes the tag definition itself — every
    entry currently carrying `name` will show `new_name` afterward.

    When to Use:
    - Fixing a typo in a tag name, or standardizing colors for an
      already-in-use tag.

    When NOT to Use:
    - To add/remove the tag from specific entries without renaming it — use
      `clickup_add_time_entry_tags` / `clickup_remove_time_entry_tags`.

    Returns:
    A confirmation string with the old and new tag names.

    Examples:
    params = {"team_id": "123", "name": "billing", "new_name": "client-billing", "tag_bg": "#BF55EC", "tag_fg": "#FFFFFF"}

    Error Handling:
    404 means no tag named `name` exists in this Workspace.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        body = {
            "name": params.name,
            "new_name": params.new_name,
            "tag_bg": params.tag_bg,
            "tag_fg": params.tag_fg,
        }
        await client.request("PUT", f"/team/{team_id}/time_entries/tags", json_body=body)
        return f"Renamed time entry tag '{params.name}' -> '{params.new_name}'."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# clickup_replace_time_estimates_by_user / clickup_update_time_estimates_by_user
# --------------------------------------------------------------------------


class ReplaceTimeEstimatesByUserInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace id. Defaults to CLICKUP_TEAM_ID.")
    task_id: str = Field(min_length=1, description="Task id (or custom task id) to set estimates on.")
    estimates: list[TimeEstimateInput] = Field(
        min_length=1,
        max_length=10,
        description="Per-assignee estimates, up to 10 per request. This REPLACES all existing per-assignee "
        "estimates on the task — any assignee omitted here has their estimate deleted.",
    )


@mcp.tool(
    name="clickup_replace_time_estimates_by_user",
    annotations=ToolAnnotations(
        title="Replace Time Estimates by User",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_replace_time_estimates_by_user(params: ReplaceTimeEstimatesByUserInput) -> str:
    """Overwrite a task's entire set of per-assignee time estimates.

    Calls `PUT /v3/workspaces/{team_id}/tasks/{task_id}/time_estimates_by_user`.
    Note: Business Plan and above only — returns 400 if the Workspace isn't
    entitled to per-assignee time estimates.

    When to Use:
    - Resetting a task's whole per-assignee estimate breakdown in one call
      (e.g. re-planning effort across the team).

    When NOT to Use:
    - To adjust just some assignees' estimates while leaving the rest
      untouched — use `clickup_update_time_estimates_by_user` instead; this
      tool deletes the estimate for any assignee you don't include.

    Returns:
    A confirmation string with the new total estimate and the per-assignee
    breakdown (in `assignee: duration` form).

    Examples:
    params = {"team_id": "123", "task_id": "abc123", "estimates": [{"assignee": 300001, "time": 3600000}]}
    params = {"team_id": "123", "task_id": "abc123", "estimates": [{"assignee": "unassigned", "time": 7200000}]}

    Error Handling:
    400 means a payload/entitlement problem (over 10 estimates, or the plan
    doesn't support this feature); 404 means the task doesn't exist, or an
    `assignee` isn't currently assigned to it.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        estimates_body = [e.to_body() for e in params.estimates]
        resp = await client.request(
            "PUT",
            f"/workspaces/{team_id}/tasks/{params.task_id}/time_estimates_by_user",
            json_body=cast(dict[str, Any], estimates_body),
            use_v3=True,
        )
        data = _safe_json(resp).get("data") or {}
        return f"Replaced time estimates for task {params.task_id}.\n\n{_format_estimates(data)}"
    except Exception as exc:
        return handle_api_error(exc)


class UpdateTimeEstimatesByUserInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace id. Defaults to CLICKUP_TEAM_ID.")
    task_id: str = Field(min_length=1, description="Task id (or custom task id) to set estimates on.")
    estimates: list[TimeEstimateInput] = Field(
        min_length=1,
        max_length=10,
        description="Per-assignee estimates, up to 10 per request. Assignees not listed here keep their "
        "existing estimate unchanged (unlike clickup_replace_time_estimates_by_user).",
    )


@mcp.tool(
    name="clickup_update_time_estimates_by_user",
    annotations=ToolAnnotations(
        title="Update Time Estimates by User",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_time_estimates_by_user(params: UpdateTimeEstimatesByUserInput) -> str:
    """Set or adjust specific assignees' time estimates on a task, leaving others unchanged.

    Calls `PATCH /v3/workspaces/{team_id}/tasks/{task_id}/time_estimates_by_user`.
    Note: Business Plan and above only — returns 400 if the Workspace isn't
    entitled to per-assignee time estimates.

    When to Use:
    - Tweaking one or two assignees' estimates without disturbing the rest
      of the breakdown.

    When NOT to Use:
    - To wipe and fully re-specify the whole breakdown — use
      `clickup_replace_time_estimates_by_user`.

    Returns:
    A confirmation string with the new total estimate and the per-assignee
    breakdown that changed.

    Examples:
    params = {"team_id": "123", "task_id": "abc123", "estimates": [{"assignee": 300001, "time": 5400000}]}

    Error Handling:
    400 means a payload/entitlement problem; 404 means the task doesn't
    exist, or an `assignee` isn't currently assigned to it.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        estimates_body = [e.to_body() for e in params.estimates]
        resp = await client.request(
            "PATCH",
            f"/workspaces/{team_id}/tasks/{params.task_id}/time_estimates_by_user",
            json_body=cast(dict[str, Any], estimates_body),
            use_v3=True,
        )
        data = _safe_json(resp).get("data") or {}
        return f"Updated time estimates for task {params.task_id}.\n\n{_format_estimates(data)}"
    except Exception as exc:
        return handle_api_error(exc)
