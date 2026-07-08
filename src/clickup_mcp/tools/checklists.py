"""Checklist tools — create/edit/delete checklists and their line items.

Wave 2 — task t8-checklists-tags. Checklists live on a single task; there is no
standalone "list checklists" endpoint (checklists are read back via the task's
own `checklists` array, exposed by `tools/tasks.py`), so this module is
mutation-only: create/edit/delete checklist + create/edit/delete checklist item.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.server import mcp


def _extract_checklist(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the `{"checklist": {...}}` wrapper ClickUp uses on writes.

    Response-shape defensiveness: some ClickUp responses echo the checklist
    directly (no wrapper); fall back to the bare payload when it looks like one.
    """
    checklist = payload.get("checklist")
    if isinstance(checklist, dict):
        return checklist
    if "id" in payload:
        return payload
    return {}


def _find_item(checklist: dict[str, Any], checklist_item_id: str) -> dict[str, Any] | None:
    items: list[dict[str, Any]] = checklist.get("items") or []
    for item in items:
        if str(item.get("id")) == checklist_item_id:
            return item
    return None


def _format_checklist(checklist: dict[str, Any], intro: str) -> str:
    lines = [intro]
    name = checklist.get("name")
    checklist_id = checklist.get("id")
    if name or checklist_id:
        lines.append(f"- Checklist: **{name or 'unnamed'}** (id `{checklist_id or 'unknown'}`)")
    items = checklist.get("items") or []
    if items:
        lines.append("- Items:")
        for item in items:
            mark = "x" if item.get("resolved") else " "
            lines.append(f"  - [{mark}] {item.get('name', 'unnamed')} (id `{item.get('id', 'unknown')}`)")
    return "\n".join(lines)


class CreateChecklistInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task to add the checklist to.")
    name: str = Field(..., min_length=1, description="Name of the new checklist.")
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
    name="clickup_create_checklist",
    annotations=ToolAnnotations(
        title="Create Checklist",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_checklist(params: CreateChecklistInput) -> str:
    """Add a new (empty) checklist to a task.

    Checklists group related to-do items on a task; add items afterwards with
    `clickup_create_checklist_item`.

    When to Use:
    - Breaking a task into a set of trackable sub-steps that are not full tasks.

    When NOT to Use:
    - For work that needs its own assignee, due date, or status — create a
      subtask instead (`clickup_create_task` with a `parent`).

    Returns:
    A confirmation string with the new checklist's name and id.

    Examples:
        params = {"task_id": "9hz", "name": "Pre-launch checks"}
        params = {"task_id": "CUST-123", "name": "QA", "custom_task_ids": True, "team_id": "123"}

    Error Handling:
    404 means the task_id does not exist; 403 with custom_task_ids commonly means
    the team_id does not match the token's Workspace.
    """
    try:
        client = get_client()
        query: dict[str, Any] | None = None
        if params.custom_task_ids:
            team_id = params.team_id or get_settings().clickup_team_id or None
            query = {"custom_task_ids": "true", "team_id": team_id}
        resp = await client.request(
            "POST",
            f"/task/{params.task_id}/checklist",
            params=query,
            json_body={"name": params.name},
        )
        checklist = _extract_checklist(resp.json())
        checklist.setdefault("name", params.name)
        return _format_checklist(checklist, f"Created checklist on task `{params.task_id}`.")
    except Exception as exc:
        return handle_api_error(exc)


class EditChecklistInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    checklist_id: str = Field(..., min_length=1, description="ID of the checklist to update.")
    name: str | None = Field(default=None, min_length=1, description="New name for the checklist.")
    position: int | None = Field(
        default=None,
        ge=0,
        description="New display order among the task's checklists; use 0 to move it to the top.",
    )

    @model_validator(mode="after")
    def _require_a_change(self) -> EditChecklistInput:
        if self.name is None and self.position is None:
            raise ValueError("Provide at least one of name or position to edit.")
        return self


@mcp.tool(
    name="clickup_edit_checklist",
    annotations=ToolAnnotations(
        title="Edit Checklist",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_checklist(params: EditChecklistInput) -> str:
    """Rename a checklist and/or reposition it among a task's other checklists.

    When to Use:
    - Renaming a checklist, or reordering checklists on a task (`position: 0`
      moves one to the top).

    When NOT to Use:
    - To resolve/reorder individual items — use `clickup_edit_checklist_item`.

    Returns:
    A confirmation string with the updated checklist's name and id.

    Examples:
        params = {"checklist_id": "b8a8...", "name": "Launch checklist"}
        params = {"checklist_id": "b8a8...", "position": 0}

    Error Handling:
    404 means the checklist_id does not exist.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.position is not None:
            body["position"] = params.position
        resp = await client.request("PUT", f"/checklist/{params.checklist_id}", json_body=body)
        checklist = _extract_checklist(resp.json())
        checklist.setdefault("id", params.checklist_id)
        return _format_checklist(checklist, "Updated checklist.")
    except Exception as exc:
        return handle_api_error(exc)


class DeleteChecklistInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    checklist_id: str = Field(..., min_length=1, description="ID of the checklist to delete.")


@mcp.tool(
    name="clickup_delete_checklist",
    annotations=ToolAnnotations(
        title="Delete Checklist",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_checklist(params: DeleteChecklistInput) -> str:
    """Permanently remove a checklist (and all of its items) from its task.

    When to Use:
    - The checklist is no longer needed.

    When NOT to Use:
    - To remove a single item, use `clickup_delete_checklist_item` instead —
      this deletes the whole checklist.

    Returns:
    A confirmation string naming the deleted checklist id.

    Error Handling:
    404 means the checklist_id does not exist (may already be deleted).
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/checklist/{params.checklist_id}")
        return f"Deleted checklist `{params.checklist_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class CreateChecklistItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    checklist_id: str = Field(..., min_length=1, description="ID of the checklist to add an item to.")
    name: str = Field(..., min_length=1, description="Text of the checklist item.")
    assignee: int | None = Field(default=None, description="ClickUp user ID to assign the item to.")


@mcp.tool(
    name="clickup_create_checklist_item",
    annotations=ToolAnnotations(
        title="Create Checklist Item",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_checklist_item(params: CreateChecklistItemInput) -> str:
    """Add a new line item to an existing checklist.

    When to Use:
    - Adding a to-do line to a checklist created with `clickup_create_checklist`.

    When NOT to Use:
    - To create the checklist itself first — use `clickup_create_checklist`.

    Returns:
    A confirmation string; when the API response includes the new item's id it
    is included, otherwise only the item name and checklist id are echoed.

    Examples:
        params = {"checklist_id": "b8a8...", "name": "Write tests"}
        params = {"checklist_id": "b8a8...", "name": "Review PR", "assignee": 183}

    Error Handling:
    404 means the checklist_id does not exist; 400 for an invalid assignee id.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name}
        if params.assignee is not None:
            body["assignee"] = params.assignee
        resp = await client.request("POST", f"/checklist/{params.checklist_id}/checklist_item", json_body=body)
        checklist = _extract_checklist(resp.json())
        created = next(
            (item for item in reversed(checklist.get("items") or []) if item.get("name") == params.name),
            None,
        )
        item_id = created.get("id", "unknown") if created else "unknown"
        return f"Added item **{params.name}** (id `{item_id}`) to checklist `{params.checklist_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class EditChecklistItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    checklist_id: str = Field(..., min_length=1, description="ID of the parent checklist.")
    checklist_item_id: str = Field(..., min_length=1, description="ID of the checklist item to edit.")
    name: str | None = Field(default=None, min_length=1, description="New text for the item.")
    resolved: bool | None = Field(default=None, description="Mark the item complete (True) or incomplete (False).")
    assignee: int | None = Field(default=None, description="ClickUp user ID to assign the item to.")
    clear_assignee: bool = Field(
        default=False, description="Set True to remove the current assignee (sends a null assignee)."
    )
    parent: str | None = Field(
        default=None,
        description="checklist_item_id of another item on the same checklist to nest this item under.",
    )
    clear_parent: bool = Field(
        default=False, description="Set True to un-nest the item back to the top level (sends a null parent)."
    )

    @model_validator(mode="after")
    def _require_a_change(self) -> EditChecklistItemInput:
        if (
            self.name is None
            and self.resolved is None
            and self.assignee is None
            and not self.clear_assignee
            and self.parent is None
            and not self.clear_parent
        ):
            raise ValueError("Provide at least one field to change (name/resolved/assignee/parent).")
        if self.assignee is not None and self.clear_assignee:
            raise ValueError("Set either assignee or clear_assignee, not both.")
        if self.parent is not None and self.clear_parent:
            raise ValueError("Set either parent or clear_parent, not both.")
        return self


@mcp.tool(
    name="clickup_edit_checklist_item",
    annotations=ToolAnnotations(
        title="Edit Checklist Item",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_checklist_item(params: EditChecklistItemInput) -> str:
    """Rename, (un)resolve, reassign, or nest/un-nest a checklist item.

    Nesting: pass `parent` (another item's checklist_item_id) to indent this
    item under it; pass `clear_parent=True` to move it back to the top level.

    When to Use:
    - Checking off progress (`resolved=True`), reassigning, or building a
      nested checklist structure.

    When NOT to Use:
    - To remove the item entirely, use `clickup_delete_checklist_item`.

    Returns:
    A confirmation string describing the applied changes.

    Examples:
        params = {"checklist_id": "b8a8...", "checklist_item_id": "9f1...", "resolved": True}
        params = {"checklist_id": "b8a8...", "checklist_item_id": "9f1...", "parent": "aa2..."}
        params = {"checklist_id": "b8a8...", "checklist_item_id": "9f1...", "clear_assignee": True}

    Error Handling:
    404 means the checklist_id or checklist_item_id does not exist.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.resolved is not None:
            body["resolved"] = params.resolved
        if params.clear_assignee:
            body["assignee"] = None
        elif params.assignee is not None:
            body["assignee"] = params.assignee
        if params.clear_parent:
            body["parent"] = None
        elif params.parent is not None:
            body["parent"] = params.parent
        resp = await client.request(
            "PUT",
            f"/checklist/{params.checklist_id}/checklist_item/{params.checklist_item_id}",
            json_body=body,
        )
        checklist = _extract_checklist(resp.json())
        item = _find_item(checklist, params.checklist_item_id)
        if item is not None:
            mark = "resolved" if item.get("resolved") else "open"
            return (
                f"Updated checklist item **{item.get('name', params.checklist_item_id)}** "
                f"(id `{params.checklist_item_id}`, now {mark}) on checklist `{params.checklist_id}`."
            )
        return f"Updated checklist item `{params.checklist_item_id}` on checklist `{params.checklist_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteChecklistItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    checklist_id: str = Field(..., min_length=1, description="ID of the parent checklist.")
    checklist_item_id: str = Field(..., min_length=1, description="ID of the checklist item to delete.")


@mcp.tool(
    name="clickup_delete_checklist_item",
    annotations=ToolAnnotations(
        title="Delete Checklist Item",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_checklist_item(params: DeleteChecklistItemInput) -> str:
    """Remove a single line item from a checklist (the checklist itself stays).

    When to Use:
    - The individual to-do is no longer relevant, but the rest of the
      checklist should remain.

    When NOT to Use:
    - To remove the whole checklist, use `clickup_delete_checklist`.

    Returns:
    A confirmation string naming the deleted item id.

    Error Handling:
    404 means the checklist_id or checklist_item_id does not exist.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/checklist/{params.checklist_id}/checklist_item/{params.checklist_item_id}")
        return f"Deleted checklist item `{params.checklist_item_id}` from checklist `{params.checklist_id}`."
    except Exception as exc:
        return handle_api_error(exc)
