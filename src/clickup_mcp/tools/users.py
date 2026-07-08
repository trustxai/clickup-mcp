"""User (Workspace member) tools — Enterprise-only invite/edit/get/remove.

Wave: t14-guests-users. Every endpoint here requires ClickUp's Enterprise plan;
non-Enterprise Workspaces get a 403 from the API, surfaced verbatim by
`handle_api_error`. Full Workspace members (this module) differ from guests
(`clickup_mcp.tools.guests`): members see everything they've been given access
to by default, while guests only see items explicitly shared with them.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, to_json
from clickup_mcp.server import mcp


def _query(**kwargs: Any) -> dict[str, Any]:
    """Build a query-param dict, dropping unset (`None`) values."""
    return {key: value for key, value in kwargs.items() if value is not None}


def _extract_user(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the invite/get/edit response shapes into one user dict.

    ClickUp nests the user under `member.user` (get), `user` (edit), or the
    first `team.members[].user` (invite) depending on the endpoint.
    """
    member = payload.get("member")
    if isinstance(member, dict):
        member_user = member.get("user")
        if isinstance(member_user, dict):
            return member_user
    user = payload.get("user")
    if isinstance(user, dict):
        return user
    team = payload.get("team")
    if isinstance(team, dict):
        members = team.get("members")
        if isinstance(members, list) and members:
            first = members[0]
            if isinstance(first, dict):
                first_user = first.get("user")
                if isinstance(first_user, dict):
                    return first_user
    return payload


def _format_user_markdown(user: dict[str, Any], title: str) -> str:
    lines = [
        f"# {title}",
        f"- id: {user.get('id', 'unknown')}",
        f"- username: {user.get('username', 'unknown')}",
        f"- email: {user.get('email', 'unknown')}",
        f"- role: {user.get('role', 'unknown')}",
    ]
    if user.get("custom_role") is not None:
        lines.append(f"- custom_role: {user['custom_role']}")
    return "\n".join(lines)


class InviteUserToWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID to invite the member into.")
    email: str = Field(description="Email address of the person to invite as a full Workspace member.")
    admin: bool = Field(description="Whether to grant the new member Workspace admin privileges.")
    custom_role_id: int | None = Field(
        default=None, description="Custom role ID to assign the member, if the Workspace defines one."
    )


@mcp.tool(
    name="clickup_invite_user_to_workspace",
    annotations=ToolAnnotations(
        title="Invite User to Workspace",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_invite_user_to_workspace(params: InviteUserToWorkspaceInput) -> str:
    """Invite a full member to a Workspace by email.

    Members are heavier-weight than guests: once added, they can see every
    Space/Folder/List they have permission to per the Workspace's sharing
    settings, rather than only items explicitly shared with them. Note:
    Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Onboarding an internal teammate who should have standard (or admin)
      access across the Workspace.

    When NOT to Use:
    - Onboarding an external collaborator who should only see specific items
      — use `clickup_invite_guest_to_workspace` instead.

    Returns:
    A confirmation string with the invited member's email and admin flag, or
    an `Error ...` string on failure.

    Examples:
    params = {"team_id": "123", "email": "newhire@example.com", "admin": False}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan (or you lack admin
    rights). 400 usually means the email is malformed or already a member.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"email": params.email, "admin": params.admin}
        if params.custom_role_id is not None:
            body["custom_role_id"] = params.custom_role_id
        resp = await client.request("POST", f"/team/{params.team_id}/user", json_body=body)
        resp.json()
        return f"Invited **{params.email}** to workspace {params.team_id} (admin={params.admin})."
    except Exception as exc:
        return handle_api_error(exc)


class EditUserOnWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID the member belongs to.")
    user_id: str = Field(description="ID of the member to edit.")
    username: str | None = Field(default=None, description="New display name for the member.")
    admin: bool | None = Field(default=None, description="Whether the member should have admin privileges.")
    custom_role_id: int | None = Field(
        default=None, description="Custom role ID to assign; pass -1 to clear any custom role."
    )


@mcp.tool(
    name="clickup_edit_user_on_workspace",
    annotations=ToolAnnotations(
        title="Edit User on Workspace",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_user_on_workspace(params: EditUserOnWorkspaceInput) -> str:
    """Update a Workspace member's username, admin flag, or custom role.

    Only the fields you pass are sent. Note: Enterprise plan only — returns
    403 on other plans.

    When to Use:
    - Renaming a member, promoting/demoting admin access, or reassigning a
      custom role.

    When NOT to Use:
    - Editing a guest's permission flags — use
      `clickup_edit_guest_on_workspace` instead.

    Returns:
    A confirmation string listing the fields that were updated, or an
    `Error ...` string on failure.

    Examples:
    params = {"team_id": "123", "user_id": "456", "admin": True}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan (or you lack admin
    rights). 404 means the user id does not exist on this Workspace.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        if params.username is not None:
            body["username"] = params.username
        if params.admin is not None:
            body["admin"] = params.admin
        if params.custom_role_id is not None:
            body["custom_role_id"] = params.custom_role_id
        resp = await client.request("PUT", f"/team/{params.team_id}/user/{params.user_id}", json_body=body)
        resp.json()
        if not body:
            return f"No fields provided — user {params.user_id} left unchanged."
        changed = ", ".join(sorted(body))
        return f"Updated user {params.user_id} on workspace {params.team_id}: {changed}."
    except Exception as exc:
        return handle_api_error(exc)


class GetUserInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID the member belongs to.")
    user_id: str = Field(description="ID of the member to look up.")
    include_shared: bool = Field(
        default=True, description="Include details of items shared with the member in the response."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_user",
    annotations=ToolAnnotations(
        title="Get User",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_user(params: GetUserInput) -> str:
    """Look up a single Workspace member's profile, role, and admin status.

    Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Checking a member's current role/admin flag before editing or removing
      them.

    When NOT to Use:
    - Listing every member of a List/Task — use `clickup_get_list_members` /
      `clickup_get_task_members` instead. Looking up a guest — use
      `clickup_get_guest`.

    Returns:
    Markdown summary (id, username, email, role, custom role) or the raw JSON
    payload when `response_format="json"`.

    Examples:
    params = {"team_id": "123", "user_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan. 404 means the user
    id does not exist on this Workspace.
    """
    try:
        client = get_client()
        resp = await client.request(
            "GET",
            f"/team/{params.team_id}/user/{params.user_id}",
            params=_query(include_shared=params.include_shared),
        )
        payload = resp.json()
        if params.response_format is ResponseFormat.JSON:
            return to_json(payload)
        user = _extract_user(payload)
        return _format_user_markdown(user, f"User {params.user_id}")
    except Exception as exc:
        return handle_api_error(exc)


class RemoveUserFromWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(description="Workspace (team) ID the member belongs to.")
    user_id: str = Field(description="ID of the member to deactivate.")


@mcp.tool(
    name="clickup_remove_user_from_workspace",
    annotations=ToolAnnotations(
        title="Remove User from Workspace",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_user_from_workspace(params: RemoveUserFromWorkspaceInput) -> str:
    """Deactivate a full member's access to a Workspace.

    Note: Enterprise plan only — returns 403 on other plans.

    When to Use:
    - Offboarding an internal teammate who should lose all Workspace access.

    When NOT to Use:
    - Revoking a guest's access — use `clickup_remove_guest_from_workspace`
      instead.

    Returns:
    A confirmation string, or an `Error ...` string on failure.

    Examples:
    params = {"team_id": "123", "user_id": "456"}

    Error Handling:
    403 means the Workspace is not on the Enterprise plan (or you lack admin
    rights). 404 means the user id does not exist on this Workspace.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/team/{params.team_id}/user/{params.user_id}")
        return f"Removed user {params.user_id} from workspace {params.team_id}."
    except Exception as exc:
        return handle_api_error(exc)
