"""List/Task member lookups, User Groups, Custom Roles, and shared hierarchy.

Wave 3 — task t13-members-groups.

NAMING TRAP (read this before touching any tool below): ClickUp's public API
calls User Groups "Teams" in its endpoint paths (`/team/{team_id}/group`,
`/group/{group_id}`, endpoint slugs `createusergroup` / `getteams1` /
`updateteam` / `deleteteam`), while `team_id` *everywhere else in this server*
means **Workspace**. The two are never the same thing:

- **Workspace** — identified by `team_id`. The org-level container: every
  Space/Folder/List/Task and every plain member lives under one Workspace.
- **User Group** — identified by `group_id` (a UUID, e.g.
  `"4bfdfcec-6f4f-40a7-b0d6-22660d51870d"`). A named, reusable subset of
  Workspace members (e.g. "QA", "Product") that ClickUp's API/endpoint names
  still call a "Team" for historical reasons.

Every tool that takes either id spells out which one it wants.
"""

from __future__ import annotations

from typing import Any, Self

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, to_json
from clickup_mcp.server import mcp

# Display cap independent of API paging (these endpoints return the whole
# collection in one response; this only bounds what we render in markdown).
MAX_DISPLAY_ROWS = 50

_BASE_ROLE_NAMES = {1: "owner", 2: "admin", 3: "member", 4: "guest"}


def _format_member(member: dict[str, Any]) -> str:
    username = member.get("username") or "unknown"
    user_id = member.get("id", "unknown")
    email = member.get("email") or "unknown"
    return f"- **{username}** (id {user_id}, {email})"


def _members_response(members: list[dict[str, Any]], fmt: ResponseFormat, title: str) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "count": len(members), "members": members})

    lines = [f"# {title}", "", f"**{len(members)}** member(s) with direct access.", ""]
    for member in members[:MAX_DISPLAY_ROWS]:
        lines.append(_format_member(member))
    if not members:
        lines.append("_No members._")
    elif len(members) > MAX_DISPLAY_ROWS:
        lines.append("")
        lines.append(f"_Showing first {MAX_DISPLAY_ROWS} of {len(members)}; use response_format=json for the rest._")
    return "\n".join(lines)


def _format_group(group: dict[str, Any]) -> str:
    name = group.get("name") or "unknown"
    group_id = group.get("id", "unknown")
    handle = group.get("handle") or ""
    handle_part = f", @{handle}" if handle else ""
    member_count = len(group.get("members") or [])
    return f"- **{name}** (group_id `{group_id}`{handle_part}) — {member_count} member(s)"


def _groups_response(groups: list[dict[str, Any]], fmt: ResponseFormat, title: str) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "count": len(groups), "groups": groups})

    lines = [f"# {title}", "", f"**{len(groups)}** User Group(s).", ""]
    for group in groups[:MAX_DISPLAY_ROWS]:
        lines.append(_format_group(group))
    if not groups:
        lines.append("_No User Groups._")
    elif len(groups) > MAX_DISPLAY_ROWS:
        lines.append("")
        lines.append(f"_Showing first {MAX_DISPLAY_ROWS} of {len(groups)}; use response_format=json for the rest._")
    return "\n".join(lines)


def _format_custom_role(role: dict[str, Any]) -> str:
    name = role.get("name") or "unknown"
    role_id = role.get("id", "unknown")
    inherited = role.get("inherited_role")
    base_name = _BASE_ROLE_NAMES.get(inherited, str(inherited)) if isinstance(inherited, int) else str(inherited)
    member_count = len(role.get("members") or [])
    return f"- **{name}** (id {role_id}) — extends `{base_name}`, {member_count} member(s)"


def _custom_roles_response(roles: list[dict[str, Any]], fmt: ResponseFormat, title: str) -> str:
    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "count": len(roles), "custom_roles": roles})

    lines = [f"# {title}", "", f"**{len(roles)}** Custom Role(s).", ""]
    for role in roles[:MAX_DISPLAY_ROWS]:
        lines.append(_format_custom_role(role))
    if not roles:
        lines.append("_No Custom Roles._")
    elif len(roles) > MAX_DISPLAY_ROWS:
        lines.append("")
        lines.append(f"_Showing first {MAX_DISPLAY_ROWS} of {len(roles)}; use response_format=json for the rest._")
    return "\n".join(lines)


def _shared_hierarchy_response(shared: dict[str, Any], fmt: ResponseFormat, title: str) -> str:
    tasks = shared.get("tasks") or []
    lists = shared.get("lists") or []
    folders = shared.get("folders") or []

    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "tasks": tasks, "lists": lists, "folders": folders})

    lines = [
        f"# {title}",
        "",
        f"**{len(tasks)}** task(s), **{len(lists)}** List(s), **{len(folders)}** Folder(s) shared directly.",
        "",
        "## Tasks",
    ]
    if tasks:
        for task in tasks[:MAX_DISPLAY_ROWS]:
            task_id = task.get("id", task) if isinstance(task, dict) else task
            lines.append(f"- {task_id}")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Lists")
    if lists:
        for lst in lists[:MAX_DISPLAY_ROWS]:
            lines.append(f"- **{lst.get('name', 'unknown')}** (id {lst.get('id', 'unknown')})")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Folders")
    if folders:
        for folder in folders[:MAX_DISPLAY_ROWS]:
            lines.append(f"- **{folder.get('name', 'unknown')}** (id {folder.get('id', 'unknown')})")
    else:
        lines.append("_None._")
    return "\n".join(lines)


class GetListMembersInput(BaseModel):
    """Input for `clickup_get_list_members`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    list_id: str = Field(..., description="The List id to look up direct members for.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_list_members",
    annotations=ToolAnnotations(
        title="Get List Members",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_list_members(params: GetListMembersInput) -> str:
    """List the Workspace members with direct access to a List.

    Calls `GET /list/{list_id}/member`. The response only includes members
    granted access directly on this List — it excludes people who can see
    the List because they belong to a User Group (`group_id`), or because
    they inherited access from the parent Folder, Space, or Workspace
    (`team_id`).

    When to Use:
    - To check who has explicit access to a specific List before assigning
      tasks or sharing sensitive content.
    - To debug "why can't user X see this List" — if they're missing here,
      check Folder/Space sharing or User Group membership instead.

    When NOT to Use:
    - To find who has explicit access to one Task, use
      `clickup_get_task_members`.
    - To manage User Group (ClickUp API name: "Team") membership, use
      `clickup_get_user_groups` / `clickup_update_user_group`.

    Returns:
    A markdown (or JSON) list of members with id, username, and email.

    Examples:
    params = {"list_id": "901300123456"}
    params = {"list_id": "901300123456", "response_format": "json"}

    Error Handling:
    404 means the List id doesn't exist or isn't visible to this token; 401
    means CLICKUP_API_TOKEN is missing or invalid.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/list/{params.list_id}/member")
        members = resp.json().get("members", [])
        return _members_response(members, params.response_format, f"List Members — List {params.list_id}")
    except Exception as exc:
        return handle_api_error(exc)


class GetTaskMembersInput(BaseModel):
    """Input for `clickup_get_task_members`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(
        ..., description="The Task id (or custom task id, with custom_task_ids=True) to look up members for."
    )
    custom_task_ids: bool = Field(
        default=False,
        description="Treat task_id as a custom task id (e.g. 'DEV-123') instead of ClickUp's internal id. "
        "Requires team_id to also be set.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace id — required when custom_task_ids=True so ClickUp can resolve the custom id. "
        "This is the Workspace id, NOT a User Group id.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_task_members",
    annotations=ToolAnnotations(
        title="Get Task Members",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_task_members(params: GetTaskMembersInput) -> str:
    """List the Workspace members with direct access to a Task.

    Calls `GET /task/{task_id}/member`. The response only includes members
    granted access directly on this Task — it excludes people who can see
    it via a User Group (`group_id`) or inherited access from the List,
    Folder, Space, or Workspace (`team_id`).

    When to Use:
    - To check who can see a specific Task before sharing sensitive detail
      in a comment or attachment.

    When NOT to Use:
    - To find who has direct access to the whole List, use
      `clickup_get_list_members`.
    - To find who is *assigned* to the Task (a different concept from who
      can *see* it), use the task-detail tools in the tasks module.

    Returns:
    A markdown (or JSON) list of members with id, username, and email.

    Examples:
    params = {"task_id": "9hz"}
    params = {"task_id": "DEV-123", "custom_task_ids": true, "team_id": "123456"}

    Error Handling:
    404 means the Task id doesn't exist or isn't visible to this token; 400
    if custom_task_ids=True but team_id is missing.
    """
    try:
        client = get_client()
        query: dict[str, Any] = {}
        if params.custom_task_ids:
            query["custom_task_ids"] = "true"
            if params.team_id:
                query["team_id"] = params.team_id
        resp = await client.request("GET", f"/task/{params.task_id}/member", params=query or None)
        members = resp.json().get("members", [])
        return _members_response(members, params.response_format, f"Task Members — Task {params.task_id}")
    except Exception as exc:
        return handle_api_error(exc)


class CreateUserGroupInput(BaseModel):
    """Input for `clickup_create_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(..., description="Workspace id that will own the new User Group. This is NOT a group_id.")
    name: str = Field(..., min_length=1, description="Display name for the new User Group, e.g. 'Product Managers'.")
    members: list[int] = Field(
        ..., min_length=1, description="User ids (Workspace member ids) to add to the new group."
    )
    handle: str | None = Field(
        default=None, description="Optional @mention handle for the group (e.g. 'productmanagers')."
    )


@mcp.tool(
    name="clickup_create_user_group",
    annotations=ToolAnnotations(
        title="Create User Group",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_user_group(params: CreateUserGroupInput) -> str:
    """Create a User Group (ClickUp's endpoint calls this a "Team") in a Workspace.

    Calls `POST /team/{team_id}/group`. Despite the path living under
    `/team/...`, `team_id` here is the **Workspace** id — the resource
    created is a **User Group**, addressed afterwards by its own `group_id`
    (a UUID, e.g. `"4bfdfcec-6f4f-40a7-b0d6-22660d51870d"`). Never confuse
    the two: `team_id` = Workspace, `group_id` = User Group.

    When to Use:
    - To create a reusable named set of members (e.g. "QA", "Product") that
      can be @mentioned or assigned as a unit across Spaces/Folders/Lists.

    When NOT to Use:
    - To add/remove members on an existing group, use
      `clickup_update_user_group` instead of deleting and recreating it.

    Returns:
    A confirmation string with the new group's group_id, name, and member
    count.

    Examples:
    params = {"team_id": "123456", "name": "Product Managers", "members": [183, 812]}

    Error Handling:
    403 if the plan doesn't support User Groups or the token lacks
    permission; 400 if `members` contains invalid user ids. Note: adding a
    view-only guest to a group can convert them to a paid guest (billing
    impact) — ClickUp's own docs warn about this.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"name": params.name, "members": params.members}
        if params.handle:
            body["handle"] = params.handle
        resp = await client.request("POST", f"/team/{params.team_id}/group", json_body=body)
        group = resp.json()
        group_id = group.get("id", "unknown")
        name = group.get("name", params.name)
        member_count = len(group.get("members") or [])
        return f"Created User Group **{name}** (group_id `{group_id}`) with {member_count} member(s)."
    except Exception as exc:
        return handle_api_error(exc)


class GetUserGroupsInput(BaseModel):
    """Input for `clickup_get_user_groups`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(..., description="Workspace id to list User Groups for. This is NOT a group_id.")
    group_ids: list[str] | None = Field(
        default=None, description="Optional filter to only these User Group ids (group_ids, not team_id)."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_user_groups",
    annotations=ToolAnnotations(
        title="Get User Groups",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_user_groups(params: GetUserGroupsInput) -> str:
    """List the User Groups in a Workspace (ClickUp's endpoint slug: "getteams1").

    Calls `GET /group?team_id=...`. NB the naming trap: the underlying
    ClickUp endpoint is historically named "Teams", but the resource
    returned is a **User Group** (`group_id`), not the Workspace itself
    (`team_id`) — pass the Workspace id via `team_id` and optionally narrow
    to specific groups via `group_ids`.

    When to Use:
    - To look up a group's `group_id` before calling
      `clickup_update_user_group` / `clickup_delete_user_group`.
    - To audit which User Groups exist in a Workspace and who is in them.

    When NOT to Use:
    - To list plain (ungrouped) Workspace members, use a Workspace-members
      tool in another module — this tool only returns User Groups.

    Returns:
    A markdown (or JSON) list of groups with group_id, name, handle, and
    member count.

    Examples:
    params = {"team_id": "123456"}
    params = {"team_id": "123456", "group_ids": ["4bfdfcec-6f4f-40a7-b0d6-22660d51870d"]}

    Error Handling:
    400/404 if team_id is missing or invalid.
    """
    try:
        client = get_client()
        query: dict[str, Any] = {"team_id": params.team_id}
        if params.group_ids:
            query["group_ids"] = params.group_ids
        resp = await client.request("GET", "/group", params=query)
        groups = resp.json().get("groups", [])
        return _groups_response(groups, params.response_format, f"User Groups — Workspace {params.team_id}")
    except Exception as exc:
        return handle_api_error(exc)


class UpdateUserGroupInput(BaseModel):
    """Input for `clickup_update_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group_id: str = Field(..., description="User Group id to update. This is NOT a Workspace/team_id.")
    name: str | None = Field(default=None, description="New display name for the group.")
    handle: str | None = Field(default=None, description="New @mention handle for the group.")
    add_member_ids: list[int] | None = Field(default=None, description="User ids to add to the group.")
    remove_member_ids: list[int] | None = Field(default=None, description="User ids to remove from the group.")

    @model_validator(mode="after")
    def _require_at_least_one_change(self) -> Self:
        if not any([self.name, self.handle, self.add_member_ids, self.remove_member_ids]):
            raise ValueError("Provide at least one of name, handle, add_member_ids, or remove_member_ids to update.")
        return self


@mcp.tool(
    name="clickup_update_user_group",
    annotations=ToolAnnotations(
        title="Update User Group",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_user_group(params: UpdateUserGroupInput) -> str:
    """Rename a User Group and/or add/remove its members.

    Calls `PUT /group/{group_id}` (ClickUp's endpoint slug is `updateteam`,
    but `group_id` here addresses a **User Group**, never a Workspace —
    do not pass a `team_id` in this field). Member changes use ClickUp's
    `{"add": [...], "rem": [...]}` shape nested under `members`; `name` and
    `handle` are replaced wholesale when provided.

    When to Use:
    - To rename a group, change its @mention handle, or add/remove members
      without deleting and recreating the group.

    When NOT to Use:
    - To create a brand-new group, use `clickup_create_user_group`.
    - To remove the group entirely, use `clickup_delete_user_group`.

    Returns:
    A confirmation string with the updated group's name, group_id, and
    resulting member count.

    Examples:
    params = {"group_id": "4bfdfcec-6f4f-40a7-b0d6-22660d51870d", "name": "QA Team"}
    params = {
        "group_id": "4bfdfcec-6f4f-40a7-b0d6-22660d51870d",
        "add_member_ids": [123456],
        "remove_member_ids": [159753],
    }

    Error Handling:
    404 if group_id doesn't exist; 403 if the token lacks permission. Note:
    adding a view-only guest to a group can convert them to a paid guest
    (billing impact) — ClickUp's own docs warn about this.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.handle is not None:
            body["handle"] = params.handle
        if params.add_member_ids is not None or params.remove_member_ids is not None:
            body["members"] = {
                "add": params.add_member_ids or [],
                "rem": params.remove_member_ids or [],
            }
        resp = await client.request("PUT", f"/group/{params.group_id}", json_body=body)
        group = resp.json()
        name = group.get("name", params.group_id)
        member_count = len(group.get("members") or [])
        return f"Updated User Group **{name}** (group_id `{params.group_id}`) — now {member_count} member(s)."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteUserGroupInput(BaseModel):
    """Input for `clickup_delete_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group_id: str = Field(..., description="User Group id to permanently delete. This is NOT a Workspace/team_id.")


@mcp.tool(
    name="clickup_delete_user_group",
    annotations=ToolAnnotations(
        title="Delete User Group",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_user_group(params: DeleteUserGroupInput) -> str:
    """Permanently delete a User Group from a Workspace.

    Calls `DELETE /group/{group_id}` (ClickUp's endpoint slug is
    `deleteteam`, but this deletes a **User Group**, not the Workspace
    itself — `group_id` is never a `team_id`). Deleting the group does not
    delete the member accounts; members keep whatever direct or inherited
    access they already had outside the group.

    When to Use:
    - To remove a stale or duplicate User Group.

    When NOT to Use:
    - To remove a single member while keeping the group, use
      `clickup_update_user_group` with `remove_member_ids` instead.

    Returns:
    A confirmation string once the group is deleted.

    Examples:
    params = {"group_id": "4bfdfcec-6f4f-40a7-b0d6-22660d51870d"}

    Error Handling:
    404 if group_id doesn't exist (it may already be deleted); 403 if the
    token lacks permission.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/group/{params.group_id}")
        return f"Deleted User Group `{params.group_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class GetCustomRolesInput(BaseModel):
    """Input for `clickup_get_custom_roles`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(..., description="Workspace id to look up Custom Roles for. This is NOT a group_id.")
    include_members: bool = Field(
        default=True, description="Include the list of user ids assigned to each Custom Role."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_custom_roles",
    annotations=ToolAnnotations(
        title="Get Custom Roles",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_custom_roles(params: GetCustomRolesInput) -> str:
    """List the Custom Roles configured for a Workspace.

    Calls `GET /team/{team_id}/customroles` (`team_id` = Workspace id; this
    endpoint has no relation to User Groups/`group_id`). Custom Roles let a
    Workspace define named permission tiers layered on top of ClickUp's base
    roles (owner/admin/member/guest); each role's `inherited_role` shows
    which base role it extends.

    Note: Custom Roles are an Enterprise plan feature — this endpoint
    returns 403 on other plans.

    When to Use:
    - To look up which Custom Role(s) exist, and who holds them, before
      assigning one to a user elsewhere.

    When NOT to Use:
    - To list User Groups (an unrelated, non-permission concept), use
      `clickup_get_user_groups`.

    Returns:
    A markdown (or JSON) list of roles with id, name, the base role they
    extend, and (when `include_members=True`) member count.

    Examples:
    params = {"team_id": "123456"}
    params = {"team_id": "123456", "include_members": false}

    Error Handling:
    403 on non-Enterprise plans; 404 if team_id is invalid.
    """
    try:
        client = get_client()
        query = {"include_members": str(params.include_members).lower()}
        resp = await client.request("GET", f"/team/{params.team_id}/customroles", params=query)
        roles = resp.json().get("custom_roles", [])
        return _custom_roles_response(roles, params.response_format, f"Custom Roles — Workspace {params.team_id}")
    except Exception as exc:
        return handle_api_error(exc)


class GetSharedHierarchyInput(BaseModel):
    """Input for `clickup_get_shared_hierarchy`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(
        ...,
        description="Workspace id to look up items shared with the authenticated user. This is NOT a group_id.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


@mcp.tool(
    name="clickup_get_shared_hierarchy",
    annotations=ToolAnnotations(
        title="Get Shared Hierarchy",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_shared_hierarchy(params: GetSharedHierarchyInput) -> str:
    """List the Tasks, Lists, and Folders individually shared with the caller.

    Calls `GET /team/{team_id}/shared` (`team_id` = Workspace id). This
    surfaces items shared directly with the authenticated user/token that
    they would not otherwise see via normal Space/Folder/List membership —
    distinct from User Group (`group_id`) membership, which grants access
    through a named group rather than a one-off share.

    When to Use:
    - To audit what has been individually shared with the current token's
      user, outside their normal Workspace hierarchy access.

    When NOT to Use:
    - To see who has access *to* a specific List/Task (the inverse
      direction — "who can see this" rather than "what's shared with me"),
      use `clickup_get_list_members` / `clickup_get_task_members`.

    Returns:
    A markdown (or JSON) summary grouped into Tasks / Lists / Folders.

    Examples:
    params = {"team_id": "123456"}

    Error Handling:
    404/401 if team_id is invalid or the token can't access the Workspace.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/team/{params.team_id}/shared")
        shared = resp.json().get("shared", {})
        return _shared_hierarchy_response(
            shared, params.response_format, f"Shared Hierarchy — Workspace {params.team_id}"
        )
    except Exception as exc:
        return handle_api_error(exc)
