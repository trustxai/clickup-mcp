"""Folders tools — Folders CRUD + folder templates (inventory C, 7 endpoints).

Wave 1 — task t2-folders. Mirrors the house style established by
`clickup_mcp.tools.health` (decorator shape, input models, docstring
sections, `-> str` return, try/except -> handle_api_error).
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, paginated_response, to_json
from clickup_mcp.server import mcp


def _extract_items(payload: Any, key: str) -> list[dict[str, Any]]:
    """Normalize a "bare array vs object keyed by `key`" API response.

    ClickUp usually wraps list endpoints as `{"<key>": [...]}` but some
    responses (and test fakes) hand back a bare array; accept either shape
    defensively and silently drop any non-object entries.
    """
    raw = payload if isinstance(payload, list) else payload.get(key, []) if isinstance(payload, dict) else []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _format_folder_line(folder: dict[str, Any]) -> str:
    """Compact one-line rendering used by `clickup_get_folders` list items."""
    name = folder.get("name", "N/A")
    folder_id = folder.get("id", "N/A")
    task_count = folder.get("task_count", "N/A")
    list_count = len(folder.get("lists") or [])
    return (
        f"- **{name}** (id `{folder_id}`) — {task_count} task(s), {list_count} list(s); "
        f"override_statuses={folder.get('override_statuses', False)}, hidden={folder.get('hidden', False)}, "
        f"archived={folder.get('archived', False)}"
    )


def _format_folder_detail(folder: dict[str, Any]) -> str:
    """Full markdown block for a single Folder, used by `clickup_get_folder`."""
    name = folder.get("name", "N/A")
    folder_id = folder.get("id", "N/A")
    space_raw = folder.get("space")
    space: dict[str, Any] = space_raw if isinstance(space_raw, dict) else {}
    statuses = folder.get("statuses") or []
    status_names = ", ".join(s.get("status", "?") for s in statuses if isinstance(s, dict)) or (
        "none (inherits the Space's statuses)"
    )
    lists = folder.get("lists") or []
    lines = [
        f"# Folder: {name}",
        "",
        f"- id: `{folder_id}`",
        f"- space: **{space.get('name', 'N/A')}** (id `{space.get('id', 'N/A')}`)",
        f"- orderindex: {folder.get('orderindex', 'N/A')}",
        f"- task_count: {folder.get('task_count', 'N/A')}",
        f"- override_statuses: {folder.get('override_statuses', False)}",
        f"- hidden: {folder.get('hidden', False)}",
        f"- archived: {folder.get('archived', False)}",
        f"- permission_level: {folder.get('permission_level', 'N/A')}",
        f"- statuses: {status_names}",
        f"- lists: {len(lists)}",
    ]
    return "\n".join(lines)


def _format_template_line(template: dict[str, Any]) -> str:
    """Compact one-line rendering used by `clickup_get_folder_templates`."""
    name = template.get("name", "N/A")
    template_id = template.get("id", "N/A")
    return f"- **{name}** (id `{template_id}`)"


class CreateFolderInput(BaseModel):
    """Body for `clickup_create_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(description="The Space id to create the Folder in.")
    name: str = Field(min_length=1, description="The new Folder's name.")


class GetFoldersInput(BaseModel):
    """Params for `clickup_get_folders`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(description="The Space id to list Folders from.")
    archived: bool = Field(default=False, description="Whether to return archived Folders instead of active ones.")
    limit: int = Field(default=20, ge=1, le=100, description="Max Folders to return in this page (client-side).")
    offset: int = Field(default=0, ge=0, description="Number of Folders to skip before this page (client-side).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class GetFolderInput(BaseModel):
    """Params for `clickup_get_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(description="The Folder id to fetch.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class UpdateFolderInput(BaseModel):
    """Body for `clickup_update_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(description="The Folder id to rename/update.")
    name: str = Field(min_length=1, description="The Folder's new name.")
    override_statuses: bool | None = Field(
        default=None,
        description=(
            "If provided, flips whether this Folder's Lists use Folder-level custom statuses "
            "instead of inheriting the Space's; omit to leave the current setting unchanged."
        ),
    )


class DeleteFolderInput(BaseModel):
    """Params for `clickup_delete_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(description="The Folder id to permanently delete.")


class FolderTemplateOptions(BaseModel):
    """Optional template-replay controls for `clickup_create_folder_from_template`.

    All fields are optional; omitted fields fall back to ClickUp's own
    defaults for the `folder_template` endpoint (notably `return_immediately`
    defaults to `true` server-side).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    return_immediately: bool | None = Field(
        default=None,
        description="Return the new Folder id immediately (true) or wait for full content creation (false).",
    )
    content: str | None = Field(default=None, description="Override description/content applied to the new Folder.")
    time_estimate: bool | None = Field(default=None, description="Carry over time estimates from the template.")
    automation: bool | None = Field(default=None, description="Carry over automations from the template.")
    include_views: bool | None = Field(default=None, description="Carry over Views from the template.")
    old_due_date: bool | None = Field(default=None, description="Keep the template's original due dates as-is.")
    old_start_date: bool | None = Field(default=None, description="Keep the template's original start dates as-is.")
    old_folder_due_date: bool | None = Field(
        default=None, description="Remap due dates relative to this new Folder's due date instead of the template's."
    )
    old_folder_start_date: bool | None = Field(
        default=None,
        description="Remap start dates relative to this new Folder's start date instead of the template's.",
    )
    comment_attachments: bool | None = Field(default=None, description="Carry over comments and attachments.")
    custom_type: bool | None = Field(default=None, description="Carry over custom task types.")
    external_dependencies: bool | None = Field(
        default=None, description="Carry over dependencies pointing outside the template."
    )
    internal_dependencies: bool | None = Field(
        default=None, description="Carry over dependencies between tasks inside the template."
    )
    owner_id: bool | None = Field(default=None, description="Carry over the original task owners.")
    priority: bool | None = Field(default=None, description="Carry over task priorities.")
    assignee: bool | None = Field(default=None, description="Carry over task assignees.")
    tags: bool | None = Field(default=None, description="Carry over Tags.")
    status: bool | None = Field(default=None, description="Carry over each task's current status.")
    subtasks: bool | None = Field(default=None, description="Carry over subtasks.")
    skip_weekends: bool | None = Field(default=None, description="Skip weekends when remapping due/start dates.")
    archived: bool | None = Field(default=None, description="Carry over archived tasks/Lists from the template.")
    replace_start_date: bool | None = Field(
        default=None, description="Replace start dates with `start_date` below instead of remapping them."
    )
    start_date: int | None = Field(default=None, description="Unix ms epoch used as the new reference start date.")
    due_date: int | None = Field(default=None, description="Unix ms epoch used as the new reference due date.")


class CreateFolderFromTemplateInput(BaseModel):
    """Body for `clickup_create_folder_from_template`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(description="The Space id to create the new Folder in.")
    template_id: str = Field(
        pattern=r"^t-", description="Folder template id, always prefixed with `t-` (from clickup_get_folder_templates)."
    )
    name: str = Field(min_length=1, description="Name for the new Folder created from the template.")
    options: FolderTemplateOptions | None = Field(
        default=None, description="Optional template-replay controls; omit for ClickUp's defaults."
    )


class GetFolderTemplatesInput(BaseModel):
    """Params for `clickup_get_folder_templates`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="The Workspace (team) id to list Folder templates from.")
    limit: int = Field(default=20, ge=1, le=100, description="Max templates to return in this page (client-side).")
    offset: int = Field(default=0, ge=0, description="Number of templates to skip before this page (client-side).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


@mcp.tool(
    name="clickup_create_folder",
    annotations=ToolAnnotations(
        title="Create Folder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_folder(params: CreateFolderInput) -> str:
    """Create a Folder inside a Space.

    Calls `POST /space/{space_id}/folder`. Folders group Lists within a
    Space; a Folder created here starts empty (use the Lists tools to add
    Lists to it once t3-lists lands). ClickUp's Create Folder endpoint only
    accepts `name` in the body — to enable Folder-level statuses, call
    `clickup_update_folder` with `override_statuses=true` after creating.

    When to Use:
    - To organize related Lists under one container inside a Space.

    When NOT to Use:
    - To create a List that doesn't need a Folder (use the folderless-list
      tool in `tools/lists.py` once available).
    - To duplicate an existing structure — use `clickup_create_folder_from_template`.

    Returns:
    A confirmation string with the new Folder's name and id, or an
    `Error ...` string describing the failure.

    Examples:
    params = {"space_id": "90130912", "name": "Q3 Launches"}

    Error Handling:
    404 means `space_id` doesn't exist or isn't accessible; 400 means the
    name is missing/invalid.
    """
    try:
        client = get_client()
        body = {"name": params.name}
        resp = await client.request("POST", f"/space/{params.space_id}/folder", json_body=body)
        folder = resp.json()
        name = folder.get("name", params.name)
        folder_id = folder.get("id", "unknown")
        return f"Created folder **{name}** (id `{folder_id}`) in space `{params.space_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_folders",
    annotations=ToolAnnotations(
        title="Get Folders",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_folders(params: GetFoldersInput) -> str:
    """List the Folders in a Space.

    Calls `GET /space/{space_id}/folder`. Returns each Folder's task/list
    counts and its `override_statuses` flag; use `clickup_get_folder` for
    the full status list and nested Lists of one Folder.

    When to Use:
    - To discover what Folders exist in a Space before creating or updating one.
    - To check which Folders already override the Space's statuses.

    When NOT to Use:
    - To fetch one Folder's full detail — use `clickup_get_folder`.
    - To list Lists that live directly in the Space (no Folder) — use the
      folderless-lists tool in `tools/lists.py` once available.

    Returns:
    A markdown or JSON list of Folders, or an `Error ...` string.

    Pagination:
    ClickUp returns the full Folder array in one response; this tool slices
    it client-side via `limit`/`offset` for context-window safety.

    Examples:
    params = {"space_id": "90130912", "archived": False, "limit": 20, "offset": 0}

    Error Handling:
    404 means `space_id` doesn't exist or isn't accessible.
    """
    try:
        client = get_client()
        resp = await client.request(
            "GET",
            f"/space/{params.space_id}/folder",
            params={"archived": "true" if params.archived else "false"},
        )
        folders = _extract_items(resp.json(), "folders")
        page = folders[params.offset : params.offset + params.limit]
        return paginated_response(
            items=page,
            total=len(folders),
            limit=params.limit,
            offset=params.offset,
            fmt=params.response_format,
            item_formatter=_format_folder_line,
            title=f"Folders in Space {params.space_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_folder",
    annotations=ToolAnnotations(
        title="Get Folder",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_folder(params: GetFolderInput) -> str:
    """Fetch one Folder, including its Lists and status workflow.

    Calls `GET /folder/{folder_id}`.

    When to Use:
    - To inspect a specific Folder's statuses, Lists, and `override_statuses`
      flag before deciding whether to update it.

    When NOT to Use:
    - To browse every Folder in a Space — use `clickup_get_folders`.

    Returns:
    A markdown detail block or JSON object, or an `Error ...` string.

    Examples:
    params = {"folder_id": "456", "response_format": "markdown"}

    Error Handling:
    404 means `folder_id` doesn't exist or isn't accessible.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/folder/{params.folder_id}")
        folder = resp.json()
        if params.response_format is ResponseFormat.JSON:
            return to_json(folder)
        return _format_folder_detail(folder)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_folder",
    annotations=ToolAnnotations(
        title="Update Folder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_folder(params: UpdateFolderInput) -> str:
    """Rename a Folder and/or toggle its status-override setting.

    Calls `PUT /folder/{folder_id}`. Setting `override_statuses=True` moves
    this Folder (and Lists inside it, unless a List overrides further) from
    inheriting the Space's status workflow to a Folder-level custom one;
    this endpoint only flips the flag — inspect the resulting statuses with
    `clickup_get_folder`. Note: the status definitions themselves cannot be
    set through the public API (same limitation as Space statuses, verified
    live for Spaces) — define custom statuses in the ClickUp UI.

    When to Use:
    - To rename a Folder.
    - To turn Folder-level status overrides on or off.

    When NOT to Use:
    - To change a single List's statuses — that is scoped to the List/Space
      tools, not this endpoint.

    Returns:
    A confirmation string with the Folder's new name, or an `Error ...` string.

    Examples:
    params = {"folder_id": "456", "name": "Q3 Launches (Renamed)", "override_statuses": True}

    Error Handling:
    404 means `folder_id` doesn't exist or isn't accessible; 400 means the
    name is missing/invalid.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name}
        if params.override_statuses is not None:
            body["override_statuses"] = params.override_statuses
        resp = await client.request("PUT", f"/folder/{params.folder_id}", json_body=body)
        folder = resp.json()
        name = folder.get("name", params.name)
        suffix = f", override_statuses={params.override_statuses}" if params.override_statuses is not None else ""
        return f"Updated folder `{params.folder_id}` — name is now **{name}**{suffix}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_folder",
    annotations=ToolAnnotations(
        title="Delete Folder",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_folder(params: DeleteFolderInput) -> str:
    """Permanently delete a Folder and every List/Task inside it.

    Calls `DELETE /folder/{folder_id}`. This cannot be undone via the API.

    When to Use:
    - To remove a Folder (and everything inside it) that is no longer needed.

    When NOT to Use:
    - To archive instead of destroy — use `clickup_update_folder` semantics
      via the Space's archive controls, not this tool, if you may need the
      data again.

    Returns:
    A confirmation string, or an `Error ...` string.

    Examples:
    params = {"folder_id": "456"}

    Error Handling:
    404 means `folder_id` doesn't exist or was already deleted.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/folder/{params.folder_id}")
        return f"Deleted folder `{params.folder_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_folder_from_template",
    annotations=ToolAnnotations(
        title="Create Folder From Template",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_folder_from_template(params: CreateFolderFromTemplateInput) -> str:
    """Create a new Folder by replaying a Folder template.

    Calls `POST /space/{space_id}/folder_template/{template_id}`. Fetch
    available template ids (always `t-`-prefixed) with
    `clickup_get_folder_templates` first.

    When to Use:
    - To stamp out a repeatable Folder structure (Lists, statuses, views)
      instead of rebuilding it by hand with `clickup_create_folder`.

    When NOT to Use:
    - To create a plain empty Folder — use `clickup_create_folder`.

    Returns:
    A confirmation string with the new Folder's id, or an `Error ...` string.
    If `options.return_immediately` is left at ClickUp's default, the
    template content may still be populating asynchronously — re-check with
    `clickup_get_folder`.

    Examples:
    params = {
        "space_id": "90130912",
        "template_id": "t-7162342",
        "name": "Q4 Launch (from template)",
        "options": {"include_views": True, "old_due_date": True},
    }

    Error Handling:
    404 means `space_id` or `template_id` doesn't exist; 400 means the name
    is missing/invalid.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name}
        if params.options is not None:
            body["options"] = params.options.model_dump(exclude_none=True)
        resp = await client.request(
            "POST",
            f"/space/{params.space_id}/folder_template/{params.template_id}",
            json_body=body,
        )
        data = resp.json()
        folder_id = data.get("id", "unknown")
        return (
            f"Created folder **{params.name}** from template `{params.template_id}` in space "
            f"`{params.space_id}` — id `{folder_id}`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_folder_templates",
    annotations=ToolAnnotations(
        title="Get Folder Templates",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_folder_templates(params: GetFolderTemplatesInput) -> str:
    """List the Folder templates available in a Workspace.

    Calls `GET /team/{team_id}/folder_template`. Template ids are
    `t-`-prefixed and feed directly into `clickup_create_folder_from_template`.

    When to Use:
    - To discover which Folder template id to pass to
      `clickup_create_folder_from_template`.

    When NOT to Use:
    - To list actual Folders in a Space — use `clickup_get_folders`.

    Returns:
    A markdown or JSON list of templates, or an `Error ...` string.

    Pagination:
    ClickUp returns the full template array in one response; this tool
    slices it client-side via `limit`/`offset` for context-window safety.

    Examples:
    params = {"team_id": "90130912", "limit": 20, "offset": 0}

    Error Handling:
    404 means `team_id` doesn't exist or isn't accessible.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/team/{params.team_id}/folder_template")
        templates = _extract_items(resp.json(), "templates")
        page = templates[params.offset : params.offset + params.limit]
        return paginated_response(
            items=page,
            total=len(templates),
            limit=params.limit,
            offset=params.offset,
            fmt=params.response_format,
            item_formatter=_format_template_line,
            title=f"Folder Templates in Workspace {params.team_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)
