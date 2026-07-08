"""Guest tools — Enterprise-only external collaborators on tasks/lists/folders.

Wave: t14-guests-users. Every endpoint here requires ClickUp's Enterprise plan;
non-Enterprise Workspaces get a 403 from the API, surfaced verbatim by
`handle_api_error`.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, to_json
from clickup_mcp.server import mcp

PermissionLevel = Literal["read", "comment", "edit", "create"]


def _query(**kwargs: Any) -> dict[str, Any]:
    """Build a query-param dict, dropping unset (`None`) values."""
    return {key: value for key, value in kwargs.items() if value is not None}


def _extract_guest(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize the guest (+ shared) sub-objects out of the various response shapes.

    ClickUp nests the guest under `guest` (get/add/remove-*) or under
    `team.guest` (invite); `shared` (tasks/lists/folders visible to the guest)
    is a sibling of `guest` when present.
    """
    guest = payload.get("guest")
    shared = payload.get("shared")
    if isinstance(guest, dict):
        return guest, shared if isinstance(shared, dict) else {}
    team = payload.get("team")
    if isinstance(team, dict) and isinstance(team.get("guest"), dict):
        return team["guest"], shared if isinstance(shared, dict) else {}
    return payload, {}


def _format_guest_markdown(guest: dict[str, Any], shared: dict[str, Any], title: str) -> str:
    raw_user = guest.get("user")
    user = raw_user if isinstance(raw_user, dict) else guest
    lines = [
        f"# {title}",
        f"- id: {user.get('id', guest.get('id', 'unknown'))}",
        f"- username: {user.get('username', 'unknown')}",
        f"- email: {user.get('email', 'unknown')}",
        "- permissions: "
        f"can_edit_tags={guest.get('can_edit_tags')}, "
        f"can_see_time_spent={guest.get('can_see_time_spent')}, "
        f"can_see_time_estimated={guest.get('can_see_time_estimated')}, "
        f"can_create_views={guest.get('can_create_views')}, "
        f"can_see_points_estimated={guest.get('can_see_points_estimated')}",
    ]
    if guest.get("custom_role_id") is not None:
        lines.append(f"- custom_role_id: {guest['custom_role_id']}")
    if shared:
        tasks = shared.get("tasks") or []
        lists = shared.get("lists") or []
        folders = shared.get("folders") or []
        lines.append(f"- shared with guest: {len(tasks)} task(s), {len(lists)} list(s), {len(folders)} folder(s)")
    return "\n".join(lines)


class InviteGuestToWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID to invite the guest into.")
    email: str = Field(description="Email address of the person to invite as a guest.")
    can_edit_tags: bool = Field(default=True, description="Whether the guest can create/edit tags.")
    can_see_time_spent: bool = Field(default=True, description="Whether the guest can see logged time.")
    can_see_time_estimated: bool = Field(default=True, description="Whether the guest can see time estimates.")
    can_create_views: bool = Field(default=True, description="Whether the guest can create views.")
    can_see_points_estimated: bool = Field(default=False, description="Whether the guest can see Scrum points.")
    custom_role_id: int | None = Field(
        default=None, description="Custom role ID to assign the guest, if the Workspace defines one."
    )


@mcp.tool(
    name="clickup_invite_guest_to_workspace",
    annotations=ToolAnnotations(
        title="Invite Guest to Workspace",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_invite_guest_to_workspace(params: InviteGuestToWorkspaceInput) -> str:
    """Invite an external guest to a Workspace by email.

    Guests are lighter-weight than full Workspace members: they only see the
    tasks/lists/folders explicitly shared with them via `clickup_add_guest_to_task`,
    `clickup_add_guest_to_list`, or `clickup_add_guest_to_folder`. Note: Enterprise
    plan only — returns 403 on other plans.

    When to Use:
    - Onboarding an external collaborator (client, contractor) who should only
      see specific items rather than the whole Workspace.

    When NOT to Use:
    - Adding a full internal team member — use `clickup_invite_user_to_workspace`
      instead (users see everything they're a member of by default).

    Returns:
    A confirmation string with the invited guest's id and email, or an
    `Error ...` string on failure.

    Examples:
    params = {"team_id": "123", "email": "contractor@example.com", "can_create_views": False}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 400 usually means the
    email is malformed or already a member/guest.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {
            "email": params.email,
            "can_edit_tags": params.can_edit_tags,
            "can_see_time_spent": params.can_see_time_spent,
            "can_see_time_estimated": params.can_see_time_estimated,
            "can_create_views": params.can_create_views,
            "can_see_points_estimated": params.can_see_points_estimated,
        }
        if params.custom_role_id is not None:
            body["custom_role_id"] = params.custom_role_id
        resp = await client.request("POST", f"/team/{params.team_id}/guest", json_body=body)
        guest, _shared = _extract_guest(resp.json())
        raw_user = guest.get("user")
        user = raw_user if isinstance(raw_user, dict) else guest
        guest_id = user.get("id", guest.get("id", "unknown"))
        return f"Invited guest **{params.email}** to workspace {params.team_id} (guest id {guest_id})."
    except Exception as exc:
        return handle_api_error(exc)


class EditGuestOnWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID the guest belongs to.")
    guest_id: str = Field(description="ID of the guest to edit.")
    can_edit_tags: bool | None = Field(default=None, description="Whether the guest can create/edit tags.")
    can_see_time_spent: bool | None = Field(default=None, description="Whether the guest can see logged time.")
    can_see_time_estimated: bool | None = Field(default=None, description="Whether the guest can see time estimates.")
    can_create_views: bool | None = Field(default=None, description="Whether the guest can create views.")
    can_see_points_estimated: bool | None = Field(default=None, description="Whether the guest can see Scrum points.")
    custom_role_id: int | None = Field(default=None, description="Custom role ID to assign the guest.")


@mcp.tool(
    name="clickup_edit_guest_on_workspace",
    annotations=ToolAnnotations(
        title="Edit Guest on Workspace",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_guest_on_workspace(params: EditGuestOnWorkspaceInput) -> str:
    """Update an existing guest's permission flags or custom role on a Workspace.

    Only the fields you pass are sent; omitted fields are left unchanged on
    ClickUp's side. Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Adjusting what a guest can see (time tracking, estimates) or do (create
      views, edit tags) without removing and re-inviting them.

    When NOT to Use:
    - Changing what items a guest can access — use the per-task/list/folder
      `clickup_add_guest_to_*` / `clickup_remove_guest_from_*` tools for that.

    Returns:
    A confirmation string listing the fields that were updated, or an
    `Error ...` string on failure.

    Examples:
    params = {"team_id": "123", "guest_id": "456", "can_see_time_spent": False}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the guest
    id does not exist on this Workspace.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        for field in (
            "can_edit_tags",
            "can_see_time_spent",
            "can_see_time_estimated",
            "can_create_views",
            "can_see_points_estimated",
            "custom_role_id",
        ):
            value = getattr(params, field)
            if value is not None:
                body[field] = value
        resp = await client.request("PUT", f"/team/{params.team_id}/guest/{params.guest_id}", json_body=body)
        resp.json()
        if not body:
            return f"No fields provided — guest {params.guest_id} left unchanged."
        changed = ", ".join(sorted(body))
        return f"Updated guest {params.guest_id} on workspace {params.team_id}: {changed}."
    except Exception as exc:
        return handle_api_error(exc)


class GetGuestInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID the guest belongs to.")
    guest_id: str = Field(description="ID of the guest to look up.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_guest",
    annotations=ToolAnnotations(
        title="Get Guest",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_guest(params: GetGuestInput) -> str:
    """Look up a guest's permission flags and what has been shared with them.

    Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Auditing what a guest can currently see/do before editing or removing them.

    When NOT to Use:
    - Looking up full Workspace members — use `clickup_get_user` instead.

    Returns:
    Markdown summary (id, username, email, permission flags, and a count of
    shared tasks/lists/folders) or the raw JSON payload when
    `response_format="json"`.

    Examples:
    params = {"team_id": "123", "guest_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the guest
    id does not exist on this Workspace.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/team/{params.team_id}/guest/{params.guest_id}")
        payload = resp.json()
        if params.response_format is ResponseFormat.JSON:
            return to_json(payload)
        guest, shared = _extract_guest(payload)
        return _format_guest_markdown(guest, shared, f"Guest {params.guest_id}")
    except Exception as exc:
        return handle_api_error(exc)


class RemoveGuestFromWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID the guest belongs to.")
    guest_id: str = Field(description="ID of the guest to remove.")


@mcp.tool(
    name="clickup_remove_guest_from_workspace",
    annotations=ToolAnnotations(
        title="Remove Guest from Workspace",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_guest_from_workspace(params: RemoveGuestFromWorkspaceInput) -> str:
    """Revoke a guest's access to an entire Workspace.

    This removes the guest from every task/list/folder they were shared on —
    it is not scoped to a single item. Note: Enterprise plan only — returns
    403 on other plans.

    When to Use:
    - Offboarding an external collaborator entirely.

    When NOT to Use:
    - Revoking access to just one task/list/folder — use
      `clickup_remove_guest_from_task` / `_list` / `_folder` instead.

    Returns:
    A confirmation string, or an `Error ...` string on failure.

    Examples:
    params = {"team_id": "123", "guest_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the guest
    id does not exist on this Workspace.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/team/{params.team_id}/guest/{params.guest_id}")
        return f"Removed guest {params.guest_id} from workspace {params.team_id}."
    except Exception as exc:
        return handle_api_error(exc)


class AddGuestToTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(description="Task ID to share with the guest.")
    guest_id: str = Field(description="ID of the guest to grant access to.")
    permission_level: PermissionLevel = Field(
        description="Access level to grant: read (view), comment, edit, or create (full access)."
    )
    include_shared: bool = Field(
        default=True, description="Include details of other items already shared with the guest in the response."
    )
    custom_task_ids: bool = Field(
        default=False, description="Treat `task_id` as a custom task ID instead of ClickUp's internal ID."
    )
    team_id: str | None = Field(default=None, description="Workspace ID; required when `custom_task_ids=true`.")

    @model_validator(mode="after")
    def _require_team_id_for_custom_task_ids(self) -> AddGuestToTaskInput:
        if self.custom_task_ids and not self.team_id:
            raise ValueError("team_id is required when custom_task_ids=true")
        return self


@mcp.tool(
    name="clickup_add_guest_to_task",
    annotations=ToolAnnotations(
        title="Add Guest to Task",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_add_guest_to_task(params: AddGuestToTaskInput) -> str:
    """Share a single task with an existing guest at a given permission level.

    The guest must already exist on the Workspace (see
    `clickup_invite_guest_to_workspace`). Note: Enterprise plan only — returns
    403 on other plans.

    When to Use:
    - Giving a guest visibility into exactly one task without exposing the
      rest of the list/folder/space.

    When NOT to Use:
    - Sharing an entire list or folder — use `clickup_add_guest_to_list` /
      `clickup_add_guest_to_folder` instead.

    Returns:
    A confirmation string with the task id, guest id, and permission level, or
    an `Error ...` string on failure.

    Examples:
    params = {"task_id": "abc123", "guest_id": "456", "permission_level": "edit"}
    params = {"task_id": "CUSTOM-1", "guest_id": "456", "permission_level": "read",
              "custom_task_ids": True, "team_id": "123"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the task
    or guest id does not exist.
    """
    try:
        client = get_client()
        query = _query(
            include_shared=params.include_shared,
            custom_task_ids=params.custom_task_ids or None,
            team_id=params.team_id if params.custom_task_ids else None,
        )
        resp = await client.request(
            "POST",
            f"/task/{params.task_id}/guest/{params.guest_id}",
            params=query,
            json_body={"permission_level": params.permission_level},
        )
        resp.json()
        return (
            f"Shared task {params.task_id} with guest {params.guest_id} (permission_level={params.permission_level})."
        )
    except Exception as exc:
        return handle_api_error(exc)


class RemoveGuestFromTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(description="Task ID to revoke the guest's access to.")
    guest_id: str = Field(description="ID of the guest to remove.")
    include_shared: bool = Field(
        default=True, description="Include details of the guest's remaining shared items in the response."
    )
    custom_task_ids: bool = Field(
        default=False, description="Treat `task_id` as a custom task ID instead of ClickUp's internal ID."
    )
    team_id: str | None = Field(default=None, description="Workspace ID; required when `custom_task_ids=true`.")

    @model_validator(mode="after")
    def _require_team_id_for_custom_task_ids(self) -> RemoveGuestFromTaskInput:
        if self.custom_task_ids and not self.team_id:
            raise ValueError("team_id is required when custom_task_ids=true")
        return self


@mcp.tool(
    name="clickup_remove_guest_from_task",
    annotations=ToolAnnotations(
        title="Remove Guest from Task",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_guest_from_task(params: RemoveGuestFromTaskInput) -> str:
    """Revoke a guest's access to a single task.

    Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Un-sharing one task from a guest while leaving their other shared
      items intact.

    When NOT to Use:
    - Removing the guest from the whole Workspace — use
      `clickup_remove_guest_from_workspace`.

    Returns:
    A confirmation string, or an `Error ...` string on failure.

    Examples:
    params = {"task_id": "abc123", "guest_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the task
    or guest id does not exist.
    """
    try:
        client = get_client()
        query = _query(
            include_shared=params.include_shared,
            custom_task_ids=params.custom_task_ids or None,
            team_id=params.team_id if params.custom_task_ids else None,
        )
        await client.request("DELETE", f"/task/{params.task_id}/guest/{params.guest_id}", params=query)
        return f"Removed guest {params.guest_id} from task {params.task_id}."
    except Exception as exc:
        return handle_api_error(exc)


class AddGuestToListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(description="List ID to share with the guest.")
    guest_id: str = Field(description="ID of the guest to grant access to.")
    permission_level: PermissionLevel = Field(
        description="Access level to grant: read (view), comment, edit, or create (full access)."
    )
    include_shared: bool = Field(
        default=True, description="Include details of other items already shared with the guest in the response."
    )


@mcp.tool(
    name="clickup_add_guest_to_list",
    annotations=ToolAnnotations(
        title="Add Guest to List",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_add_guest_to_list(params: AddGuestToListInput) -> str:
    """Share a List with an existing guest at a given permission level.

    The guest gains visibility into every task in the List. Note: Enterprise
    plan only — returns 403 on other plans.

    When to Use:
    - Giving a guest access to a whole List of tasks at once.

    When NOT to Use:
    - Sharing just one task — use `clickup_add_guest_to_task`. Sharing an
      entire Folder — use `clickup_add_guest_to_folder`.

    Returns:
    A confirmation string with the list id, guest id, and permission level, or
    an `Error ...` string on failure.

    Examples:
    params = {"list_id": "789", "guest_id": "456", "permission_level": "comment"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the list
    or guest id does not exist.
    """
    try:
        client = get_client()
        resp = await client.request(
            "POST",
            f"/list/{params.list_id}/guest/{params.guest_id}",
            params=_query(include_shared=params.include_shared),
            json_body={"permission_level": params.permission_level},
        )
        resp.json()
        return (
            f"Shared list {params.list_id} with guest {params.guest_id} (permission_level={params.permission_level})."
        )
    except Exception as exc:
        return handle_api_error(exc)


class RemoveGuestFromListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(description="List ID to revoke the guest's access to.")
    guest_id: str = Field(description="ID of the guest to remove.")
    include_shared: bool = Field(
        default=True, description="Include details of the guest's remaining shared items in the response."
    )


@mcp.tool(
    name="clickup_remove_guest_from_list",
    annotations=ToolAnnotations(
        title="Remove Guest from List",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_guest_from_list(params: RemoveGuestFromListInput) -> str:
    """Revoke a guest's access to a List.

    Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Un-sharing a whole List from a guest while leaving their other shared
      items intact.

    When NOT to Use:
    - Removing just one task — use `clickup_remove_guest_from_task`.

    Returns:
    A confirmation string, or an `Error ...` string on failure.

    Examples:
    params = {"list_id": "789", "guest_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the list
    or guest id does not exist.
    """
    try:
        client = get_client()
        await client.request(
            "DELETE",
            f"/list/{params.list_id}/guest/{params.guest_id}",
            params=_query(include_shared=params.include_shared),
        )
        return f"Removed guest {params.guest_id} from list {params.list_id}."
    except Exception as exc:
        return handle_api_error(exc)


class AddGuestToFolderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(description="Folder ID to share with the guest.")
    guest_id: str = Field(description="ID of the guest to grant access to.")
    permission_level: PermissionLevel = Field(
        description="Access level to grant: read (view), comment, edit, or create (full access)."
    )
    include_shared: bool = Field(
        default=True, description="Include details of other items already shared with the guest in the response."
    )


@mcp.tool(
    name="clickup_add_guest_to_folder",
    annotations=ToolAnnotations(
        title="Add Guest to Folder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_add_guest_to_folder(params: AddGuestToFolderInput) -> str:
    """Share a Folder with an existing guest at a given permission level.

    The guest gains visibility into every List and task in the Folder. Note:
    Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Giving a guest access to an entire Folder (all its Lists and tasks) at once.

    When NOT to Use:
    - Sharing just one List — use `clickup_add_guest_to_list`. Sharing just
      one task — use `clickup_add_guest_to_task`.

    Returns:
    A confirmation string with the folder id, guest id, and permission level,
    or an `Error ...` string on failure.

    Examples:
    params = {"folder_id": "321", "guest_id": "456", "permission_level": "create"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the
    folder or guest id does not exist.
    """
    try:
        client = get_client()
        resp = await client.request(
            "POST",
            f"/folder/{params.folder_id}/guest/{params.guest_id}",
            params=_query(include_shared=params.include_shared),
            json_body={"permission_level": params.permission_level},
        )
        resp.json()
        return (
            f"Shared folder {params.folder_id} with guest {params.guest_id} "
            f"(permission_level={params.permission_level})."
        )
    except Exception as exc:
        return handle_api_error(exc)


class RemoveGuestFromFolderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(description="Folder ID to revoke the guest's access to.")
    guest_id: str = Field(description="ID of the guest to remove.")
    include_shared: bool = Field(
        default=True, description="Include details of the guest's remaining shared items in the response."
    )


@mcp.tool(
    name="clickup_remove_guest_from_folder",
    annotations=ToolAnnotations(
        title="Remove Guest from Folder",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_guest_from_folder(params: RemoveGuestFromFolderInput) -> str:
    """Revoke a guest's access to a Folder.

    Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Un-sharing a whole Folder from a guest while leaving their other shared
      items intact.

    When NOT to Use:
    - Removing just one List or task — use `clickup_remove_guest_from_list` /
      `clickup_remove_guest_from_task`.

    Returns:
    A confirmation string, or an `Error ...` string on failure.

    Examples:
    params = {"folder_id": "321", "guest_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the
    folder or guest id does not exist.
    """
    try:
        client = get_client()
        await client.request(
            "DELETE",
            f"/folder/{params.folder_id}/guest/{params.guest_id}",
            params=_query(include_shared=params.include_shared),
        )
        return f"Removed guest {params.guest_id} from folder {params.folder_id}."
    except Exception as exc:
        return handle_api_error(exc)
