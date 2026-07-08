"""Space tag tools — manage a Space's task-tag palette and tag tasks with it.

Wave 2 — task t8-checklists-tags. Tags are defined per-Space (`tag_fg`/`tag_bg`
hex colors) and then attached to/removed from individual tasks by name.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, to_json
from clickup_mcp.server import mcp

_HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


def _extract_tags(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the `{"tags": [...]}` wrapper (response-shape defensiveness)."""
    tags = payload.get("tags")
    if isinstance(tags, list):
        return tags
    return []


def _format_tag(tag: dict[str, Any]) -> str:
    name = tag.get("name", "unnamed")
    fg = tag.get("tag_fg", "N/A")
    bg = tag.get("tag_bg", "N/A")
    return f"- **{name}** — fg `{fg}`, bg `{bg}`"


def _task_tag_query(custom_task_ids: bool, team_id: str | None) -> dict[str, Any] | None:
    """Build the shared `custom_task_ids`/`team_id` query pair for task-tag ops."""
    if not custom_task_ids:
        return None
    return {"custom_task_ids": "true", "team_id": team_id or get_settings().clickup_team_id or None}


class GetSpaceTagsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="ID of the Space to list tags for.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_space_tags",
    annotations=ToolAnnotations(
        title="Get Space Tags",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_space_tags(params: GetSpaceTagsInput) -> str:
    """List every task Tag defined in a Space, with its foreground/background colors.

    Space tags are not paginated by the API — this returns the full palette.

    When to Use:
    - Discovering existing tag names/colors before creating or applying one.

    When NOT to Use:
    - To see which tags are on a specific task — use `clickup_get_task`.

    Returns:
    Markdown bullet list (name + fg/bg colors) or JSON array, per response_format.

    Examples:
        params = {"space_id": "90130912"}
        params = {"space_id": "90130912", "response_format": "json"}

    Error Handling:
    404 means the space_id does not exist.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/space/{params.space_id}/tag")
        tags = _extract_tags(resp.json())
        if params.response_format is ResponseFormat.JSON:
            return to_json({"space_id": params.space_id, "count": len(tags), "tags": tags})
        lines = [f"# Tags in Space `{params.space_id}`", "", f"Showing **{len(tags)}** tag(s).", ""]
        lines.extend(_format_tag(tag) for tag in tags)
        if not tags:
            lines.append("_No tags defined in this Space._")
        return "\n".join(lines)
    except Exception as exc:
        return handle_api_error(exc)


class CreateSpaceTagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="ID of the Space to add the tag to.")
    name: str = Field(..., min_length=1, description="Tag name.")
    tag_fg: str | None = Field(
        default=None, pattern=_HEX_COLOR_PATTERN, description="Foreground (text) hex color, e.g. #FFFFFF."
    )
    tag_bg: str | None = Field(
        default=None, pattern=_HEX_COLOR_PATTERN, description="Background hex color, e.g. #FF0000."
    )


@mcp.tool(
    name="clickup_create_space_tag",
    annotations=ToolAnnotations(
        title="Create Space Tag",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_space_tag(params: CreateSpaceTagInput) -> str:
    """Add a new task Tag (with optional colors) to a Space's tag palette.

    When to Use:
    - Defining a new tag before applying it to tasks with `clickup_add_tag_to_task`.

    When NOT to Use:
    - To rename or recolor an existing tag — use `clickup_edit_space_tag`.

    Returns:
    A confirmation string with the tag's name and colors.

    Examples:
        params = {"space_id": "90130912", "name": "urgent"}
        params = {"space_id": "90130912", "name": "urgent", "tag_fg": "#FFFFFF", "tag_bg": "#FF0000"}

    Error Handling:
    404 means the space_id does not exist; 400 for a duplicate tag name.
    """
    try:
        client = get_client()
        tag: dict[str, Any] = {"name": params.name}
        if params.tag_fg is not None:
            tag["tag_fg"] = params.tag_fg
        if params.tag_bg is not None:
            tag["tag_bg"] = params.tag_bg
        await client.request("POST", f"/space/{params.space_id}/tag", json_body={"tag": tag})
        color_note = f" (fg `{params.tag_fg}`, bg `{params.tag_bg}`)" if params.tag_fg or params.tag_bg else ""
        return f"Created tag **{params.name}**{color_note} in Space `{params.space_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class EditSpaceTagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="ID of the Space the tag belongs to.")
    tag_name: str = Field(..., min_length=1, description="Current name of the tag to update.")
    new_name: str | None = Field(default=None, min_length=1, description="New name for the tag (omit to keep it).")
    tag_fg: str | None = Field(
        default=None, pattern=_HEX_COLOR_PATTERN, description="New foreground hex color, e.g. #FFFFFF."
    )
    tag_bg: str | None = Field(
        default=None, pattern=_HEX_COLOR_PATTERN, description="New background hex color, e.g. #FF0000."
    )


@mcp.tool(
    name="clickup_edit_space_tag",
    annotations=ToolAnnotations(
        title="Edit Space Tag",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_space_tag(params: EditSpaceTagInput) -> str:
    """Rename and/or recolor an existing Space tag.

    The tag_name path segment is URL-encoded automatically, so names with
    spaces or special characters are safe to pass as-is.

    When to Use:
    - Fixing a tag's colors, or renaming it (renaming updates it everywhere
      it's already applied to tasks).

    When NOT to Use:
    - To create a brand-new tag — use `clickup_create_space_tag`.

    Returns:
    A confirmation string with the tag's (possibly new) name and colors.

    Examples:
        params = {"space_id": "90130912", "tag_name": "urgent", "tag_bg": "#990000"}
        params = {"space_id": "90130912", "tag_name": "urgent", "new_name": "critical"}

    Error Handling:
    404 means the space_id or tag_name does not exist.
    """
    try:
        client = get_client()
        tag: dict[str, Any] = {"name": params.new_name or params.tag_name}
        if params.tag_fg is not None:
            tag["tag_fg"] = params.tag_fg
        if params.tag_bg is not None:
            tag["tag_bg"] = params.tag_bg
        encoded_name = quote(params.tag_name, safe="")
        await client.request("PUT", f"/space/{params.space_id}/tag/{encoded_name}", json_body={"tag": tag})
        final_name = params.new_name or params.tag_name
        return f"Updated tag **{final_name}** in Space `{params.space_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteSpaceTagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="ID of the Space the tag belongs to.")
    tag_name: str = Field(..., min_length=1, description="Name of the tag to delete.")


@mcp.tool(
    name="clickup_delete_space_tag",
    annotations=ToolAnnotations(
        title="Delete Space Tag",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_space_tag(params: DeleteSpaceTagInput) -> str:
    """Remove a tag from a Space's palette (and from every task carrying it).

    The tag_name path segment is URL-encoded automatically.

    When to Use:
    - The tag is unused or being retired.

    When NOT to Use:
    - To just remove the tag from one task — use `clickup_remove_tag_from_task`.

    Returns:
    A confirmation string naming the deleted tag.

    Error Handling:
    404 means the space_id or tag_name does not exist (may already be deleted).
    """
    try:
        client = get_client()
        encoded_name = quote(params.tag_name, safe="")
        await client.request(
            "DELETE",
            f"/space/{params.space_id}/tag/{encoded_name}",
            json_body={"tag": {"name": params.tag_name}},
        )
        return f"Deleted tag **{params.tag_name}** from Space `{params.space_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class AddTagToTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task to tag.")
    tag_name: str = Field(..., min_length=1, description="Name of an existing Space tag to apply.")
    custom_task_ids: bool = Field(
        default=False,
        description="Set True to treat task_id as a custom task ID instead of a ClickUp task ID.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace (team) ID; required when custom_task_ids is True. Falls back to "
        "CLICKUP_TEAM_ID if omitted.",
    )


@mcp.tool(
    name="clickup_add_tag_to_task",
    annotations=ToolAnnotations(
        title="Add Tag To Task",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_add_tag_to_task(params: AddTagToTaskInput) -> str:
    """Apply an existing Space tag to a task.

    The tag must already exist in the task's Space — create it first with
    `clickup_create_space_tag` if needed.

    When to Use:
    - Labeling a task with a tag for filtering/reporting.

    When NOT to Use:
    - To define a brand-new tag — use `clickup_create_space_tag` first.

    Returns:
    A confirmation string naming the applied tag and task.

    Examples:
        params = {"task_id": "9hz", "tag_name": "urgent"}
        params = {"task_id": "CUST-123", "tag_name": "urgent", "custom_task_ids": True, "team_id": "123"}

    Error Handling:
    404 if the task_id or tag_name does not exist (the tag must already be
    defined on the task's Space).
    """
    try:
        client = get_client()
        query = _task_tag_query(params.custom_task_ids, params.team_id)
        encoded_name = quote(params.tag_name, safe="")
        await client.request("POST", f"/task/{params.task_id}/tag/{encoded_name}", params=query)
        return f"Added tag **{params.tag_name}** to task `{params.task_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class RemoveTagFromTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task to untag.")
    tag_name: str = Field(..., min_length=1, description="Name of the tag to remove from the task.")
    custom_task_ids: bool = Field(
        default=False,
        description="Set True to treat task_id as a custom task ID instead of a ClickUp task ID.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace (team) ID; required when custom_task_ids is True. Falls back to "
        "CLICKUP_TEAM_ID if omitted.",
    )


@mcp.tool(
    name="clickup_remove_tag_from_task",
    annotations=ToolAnnotations(
        title="Remove Tag From Task",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_tag_from_task(params: RemoveTagFromTaskInput) -> str:
    """Remove a tag from a task without deleting the tag from the Space.

    When to Use:
    - The task no longer belongs under that tag.

    When NOT to Use:
    - To delete the tag everywhere — use `clickup_delete_space_tag`.

    Returns:
    A confirmation string naming the removed tag and task.

    Examples:
        params = {"task_id": "9hz", "tag_name": "urgent"}

    Error Handling:
    404 if the task_id does not exist or the tag was not applied to it.
    """
    try:
        client = get_client()
        query = _task_tag_query(params.custom_task_ids, params.team_id)
        encoded_name = quote(params.tag_name, safe="")
        await client.request("DELETE", f"/task/{params.task_id}/tag/{encoded_name}", params=query)
        return f"Removed tag **{params.tag_name}** from task `{params.task_id}`."
    except Exception as exc:
        return handle_api_error(exc)
