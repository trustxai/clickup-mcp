"""Spaces tool module — Space CRUD, ClickApp feature toggles, and status workflows.

Wave 1 — task t1-spaces. A Space is the top-level container inside a Workspace
(Team); Folders and Lists live inside it. ClickUp has no dedicated "statuses"
API — the *only* way to manage a Space's status workflow is through the
`statuses` array on Create/Update Space, alongside the `features` object that
toggles ClickApps (due dates, time tracking, tags, …). See the Examples:
blocks below for both.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, paginated_response, to_json
from clickup_mcp.server import mcp

# Context-window guard: cap displayed rows independent of the (client-side)
# `limit` a caller requests, keeping responses well under the MCP payload cap.
MAX_DISPLAY_ROWS = 50


def _resolve_team_id(team_id: str | None) -> str:
    """Fall back to CLICKUP_TEAM_ID when the caller omits `team_id`."""
    if team_id:
        return team_id
    default_team_id = get_settings().clickup_team_id
    if default_team_id:
        return default_team_id
    raise RuntimeError(
        "No team_id provided and CLICKUP_TEAM_ID is not configured. Pass team_id explicitly "
        "or set CLICKUP_TEAM_ID in the environment/.env."
    )


class FeatureToggle(BaseModel):
    """Generic on/off switch for a ClickApp."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    enabled: bool = Field(default=True, description="Whether this ClickApp is turned on for the Space.")


class DueDatesFeature(FeatureToggle):
    """The `due_dates` ClickApp — has extra behavior flags beyond a plain toggle."""

    start_date: bool = Field(default=False, description="Show a start-date field on tasks in addition to due date.")
    remap_due_dates: bool = Field(
        default=False,
        description="When a task's dates change, shift dependent tasks' due dates by the same offset.",
    )
    remap_closed_due_date: bool = Field(
        default=False, description="Set a task's due date to the moment it is marked complete."
    )


class SpaceFeatures(BaseModel):
    """The `features` object accepted by Create/Update Space.

    This is the only way to toggle ClickApps per Space — there is no separate
    ClickApps endpoint. Unset fields are omitted from the outgoing request
    body so ClickUp leaves them at their current/default value.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    due_dates: DueDatesFeature | None = Field(default=None, description="Due Dates ClickApp configuration.")
    time_tracking: FeatureToggle | None = Field(default=None, description="Time Tracking ClickApp toggle.")
    tags: FeatureToggle | None = Field(default=None, description="Task Tags ClickApp toggle.")
    time_estimates: FeatureToggle | None = Field(default=None, description="Time Estimates ClickApp toggle.")
    checklists: FeatureToggle | None = Field(default=None, description="Checklists ClickApp toggle.")
    custom_fields: FeatureToggle | None = Field(default=None, description="Custom Fields ClickApp toggle.")
    remap_dependencies: FeatureToggle | None = Field(
        default=None, description="Shift dependent task dates when a dependency's dates change."
    )
    dependency_warning: FeatureToggle | None = Field(
        default=None, description="Warn when scheduling conflicts with a task dependency."
    )
    portfolios: FeatureToggle | None = Field(default=None, description="Portfolios ClickApp toggle.")

    def to_body(self) -> dict[str, Any]:
        """Serialize only the ClickApps the caller explicitly set."""
        fields: dict[str, FeatureToggle | None] = {
            "due_dates": self.due_dates,
            "time_tracking": self.time_tracking,
            "tags": self.tags,
            "time_estimates": self.time_estimates,
            "checklists": self.checklists,
            "custom_fields": self.custom_fields,
            "remap_dependencies": self.remap_dependencies,
            "dependency_warning": self.dependency_warning,
            "portfolios": self.portfolios,
        }
        return {name: value.model_dump() for name, value in fields.items() if value is not None}


class SpaceStatus(BaseModel):
    """One entry in a Space-level custom status workflow (`statuses` array)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: str = Field(..., min_length=1, description="Status label as it appears on task cards, e.g. 'to do'.")
    type: Literal["open", "custom", "closed"] = Field(
        ...,
        description="Status category: 'open' (workflow start), 'custom' (in-progress-style), or 'closed' (done).",
    )
    orderindex: int = Field(..., ge=0, description="Position in the workflow, 0-based, lowest first.")
    color: str = Field(..., min_length=1, description="Hex color for the status chip, e.g. '#d3d3d3'.")


class CreateSpaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(
        default=None,
        description="Workspace (Team) id to create the Space in. Defaults to CLICKUP_TEAM_ID if omitted.",
    )
    name: str = Field(..., min_length=1, description="Name of the new Space.")
    multiple_assignees: bool = Field(default=True, description="Allow more than one assignee per task in this Space.")
    features: SpaceFeatures | None = Field(
        default=None,
        description="ClickApp toggles (due_dates, time_tracking, tags, …). Omit to use ClickUp's defaults.",
    )
    statuses: list[SpaceStatus] | None = Field(
        default=None,
        description=(
            "Custom status workflow to replace ClickUp's four default statuses. There is no "
            "dedicated statuses endpoint — this is the only way to set them."
        ),
    )

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name, "multiple_assignees": self.multiple_assignees}
        if self.features is not None:
            body["features"] = self.features.to_body()
        if self.statuses is not None:
            body["statuses"] = [status.model_dump() for status in self.statuses]
        return body


class GetSpacesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(
        default=None,
        description="Workspace (Team) id to list Spaces from. Defaults to CLICKUP_TEAM_ID if omitted.",
    )
    archived: bool = Field(default=False, description="Return archived Spaces instead of active ones.")
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description=f"Max Spaces to display per page (client-side; further capped at {MAX_DISPLAY_ROWS}).",
    )
    offset: int = Field(default=0, ge=0, description="Number of Spaces to skip before the page begins (client-side).")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown list or raw json."
    )


class GetSpaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="Space id to retrieve.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown detail or raw json."
    )


class UpdateSpaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="Space id to update.")
    name: str | None = Field(default=None, min_length=1, description="New Space name.")
    color: str | None = Field(default=None, min_length=1, description="Hex color for the Space icon, e.g. '#e78100'.")
    private: bool | None = Field(default=None, description="Restrict the Space to explicitly-added members.")
    admin_can_manage: bool | None = Field(
        default=None,
        description="Enterprise plan only — let Workspace admins manage this private Space regardless of membership.",
    )
    multiple_assignees: bool | None = Field(
        default=None, description="Allow more than one assignee per task in this Space."
    )
    features: SpaceFeatures | None = Field(
        default=None, description="ClickApp toggles to change; fields left unset are not sent."
    )
    statuses: list[SpaceStatus] | None = Field(
        default=None,
        description="Replace the Space's status workflow with this array — the only way to edit statuses.",
    )

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.name is not None:
            body["name"] = self.name
        if self.color is not None:
            body["color"] = self.color
        if self.private is not None:
            body["private"] = self.private
        if self.admin_can_manage is not None:
            body["admin_can_manage"] = self.admin_can_manage
        if self.multiple_assignees is not None:
            body["multiple_assignees"] = self.multiple_assignees
        if self.features is not None:
            body["features"] = self.features.to_body()
        if self.statuses is not None:
            body["statuses"] = [status.model_dump() for status in self.statuses]
        return body


class DeleteSpaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_id: str = Field(..., min_length=1, description="Space id to permanently delete.")


def _format_space_summary(space: dict[str, Any]) -> str:
    """One-line item formatter used by the Spaces list."""
    name = space.get("name", "unknown")
    space_id = space.get("id", "unknown")
    private = space.get("private", False)
    multiple_assignees = space.get("multiple_assignees", False)
    return f"- **{name}** (id `{space_id}`) — private: {private}, multiple_assignees: {multiple_assignees}"


def _format_statuses(statuses: list[dict[str, Any]]) -> list[str]:
    if not statuses:
        return ["_No custom statuses — using ClickUp defaults._"]
    return [
        f"- `{st.get('status', '?')}` (type: {st.get('type', '?')}, order {st.get('orderindex', '?')}, "
        f"color {st.get('color', '?')})"
        for st in statuses
    ]


def _format_features(features: dict[str, Any]) -> list[str]:
    if not features:
        return ["_No feature data returned._"]
    enabled = sorted(name for name, cfg in features.items() if isinstance(cfg, dict) and cfg.get("enabled"))
    disabled = sorted(name for name, cfg in features.items() if isinstance(cfg, dict) and not cfg.get("enabled"))
    return [
        f"- **Enabled**: {', '.join(enabled) or 'none'}",
        f"- **Disabled**: {', '.join(disabled) or 'none'}",
    ]


def _format_space_detail(space: dict[str, Any]) -> str:
    name = space.get("name", "unknown")
    space_id = space.get("id", "unknown")
    lines = [
        f"# Space: {name}",
        "",
        f"- **id**: `{space_id}`",
        f"- **private**: {space.get('private', False)}",
        f"- **multiple_assignees**: {space.get('multiple_assignees', False)}",
        "",
        "**Statuses:**",
        *_format_statuses(space.get("statuses") or []),
        "",
        "**ClickApps:**",
        *_format_features(space.get("features") or {}),
    ]
    return "\n".join(lines)


@mcp.tool(
    name="clickup_create_space",
    annotations=ToolAnnotations(
        title="Create ClickUp Space",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_space(params: CreateSpaceInput) -> str:
    """Create a new Space inside a Workspace.

    A Space is the top-level container below a Workspace (Team) — Folders and
    Lists live inside it. Use `features` to toggle ClickApps (due dates, time
    tracking, tags, …) and `statuses` to set a custom status workflow —
    ClickUp has no dedicated statuses endpoint, so this is the only way to
    define them at creation time (or later via `clickup_update_space`).

    When to Use:
    - Setting up a new top-level area of work (e.g. a new team or project line).
    - Provisioning a Space with a specific ClickApp/status configuration up front.

    When NOT to Use:
    - To create a Folder or List inside an existing Space (use the folders/lists tools).
    - To change statuses/features on a Space that already exists (use `clickup_update_space`).

    Returns:
    A one-line confirmation naming the new Space and its id, or an `Error ...`
    string describing the failure.

    Examples:
    params = {
        "name": "Engineering",
        "multiple_assignees": True,
        "features": {"due_dates": {"enabled": True}, "time_tracking": {"enabled": False}},
        "statuses": [
            {"status": "to do", "type": "open", "orderindex": 0, "color": "#d3d3d3"},
            {"status": "in progress", "type": "custom", "orderindex": 1, "color": "#a875ff"},
            {"status": "done", "type": "closed", "orderindex": 2, "color": "#6bc950"},
        ],
    }

    Error Handling:
    400 means a malformed features/statuses payload; 401/403 mean the token
    lacks access to the Workspace.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("POST", f"/team/{team_id}/space", json_body=params.to_body())
        space = resp.json()
        space_name = space.get("name", params.name)
        space_id = space.get("id", "unknown")
        return f"Created Space **{space_name}** (id `{space_id}`) in Workspace `{team_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_spaces",
    annotations=ToolAnnotations(
        title="List ClickUp Spaces",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_spaces(params: GetSpacesInput) -> str:
    """List the Spaces in a Workspace.

    When to Use:
    - Discovering what Spaces exist before drilling into Folders/Lists.
    - Checking archived Spaces with `archived=True`.

    When NOT to Use:
    - To fetch a single Space's full detail, including statuses/features (use `clickup_get_space`).

    Returns:
    A markdown list (or JSON) of Spaces with id/private/multiple_assignees, one row per Space.

    Pagination:
    ClickUp does not paginate this endpoint — it returns every Space in one
    response. `limit`/`offset` slice that response client-side, and `limit` is
    further capped at MAX_DISPLAY_ROWS (50) regardless of the requested value,
    to keep the result well under the MCP response-size budget.

    Examples:
    params = {"team_id": "90130000000", "archived": False, "limit": 20, "offset": 0}

    Error Handling:
    401/403 mean the token can't see this Workspace; 404 means team_id is wrong.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request(
            "GET", f"/team/{team_id}/space", params={"archived": "true" if params.archived else "false"}
        )
        spaces = resp.json().get("spaces", [])
        total = len(spaces)
        effective_limit = min(params.limit, MAX_DISPLAY_ROWS)
        page = spaces[params.offset : params.offset + effective_limit]
        return paginated_response(
            items=page,
            total=total,
            limit=effective_limit,
            offset=params.offset,
            fmt=params.response_format,
            item_formatter=_format_space_summary,
            title=f"Spaces in Workspace {team_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_space",
    annotations=ToolAnnotations(
        title="Get ClickUp Space",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_space(params: GetSpaceInput) -> str:
    """Fetch full detail for a single Space, including its status workflow and ClickApp toggles.

    When to Use:
    - Inspecting a Space's current statuses/features before calling `clickup_update_space`.
    - Confirming a Space exists and getting its exact name/id.

    When NOT to Use:
    - To enumerate all Spaces in a Workspace (use `clickup_get_spaces`).

    Returns:
    A markdown detail block (or JSON) with id, private, multiple_assignees,
    the full statuses array, and which ClickApps are enabled/disabled.

    Examples:
    params = {"space_id": "90130012345"}

    Error Handling:
    404 means the space_id does not exist or the token can't see it.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/space/{params.space_id}")
        space = resp.json()
        if params.response_format is ResponseFormat.JSON:
            return to_json(space)
        return _format_space_detail(space)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_space",
    annotations=ToolAnnotations(
        title="Update ClickUp Space",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_space(params: UpdateSpaceInput) -> str:
    """Rename a Space, change its color/privacy, or replace its ClickApp/status configuration.

    Only fields you set are sent — omitted fields are left unchanged
    server-side. `statuses` and `features` are ClickUp's only way to manage a
    Space's status workflow and ClickApp toggles (there is no separate
    statuses/ClickApps API).

    When to Use:
    - Renaming a Space or toggling ClickApps (due dates, time tracking, tags, …).
    - Replacing the status workflow, e.g. adding a "blocked" custom status.
    - Making a Space private, or (Enterprise) setting `admin_can_manage`.

    When NOT to Use:
    - To change a Folder's or List's own status override (use the folders/lists tools).
    - To delete a Space (use `clickup_delete_space`).

    Returns:
    A one-line confirmation naming the Space and which fields changed, or an `Error ...` string.

    Examples:
    params = {
        "space_id": "90130012345",
        "statuses": [
            {"status": "to do", "type": "open", "orderindex": 0, "color": "#d3d3d3"},
            {"status": "blocked", "type": "custom", "orderindex": 1, "color": "#e50000"},
            {"status": "done", "type": "closed", "orderindex": 2, "color": "#6bc950"},
        ],
    }

    Error Handling:
    400 means a malformed features/statuses payload; 404 means space_id is wrong.
    """
    try:
        client = get_client()
        body = params.to_body()
        resp = await client.request("PUT", f"/space/{params.space_id}", json_body=body)
        space = resp.json()
        space_name = space.get("name", params.space_id)
        changed = ", ".join(sorted(body)) or "nothing"
        return f"Updated Space **{space_name}** (id `{params.space_id}`) — changed: {changed}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_space",
    annotations=ToolAnnotations(
        title="Delete ClickUp Space",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_space(params: DeleteSpaceInput) -> str:
    """Permanently delete a Space and everything inside it.

    This removes every Folder, folderless List, and Task in the Space.
    Deleting is irreversible from this API surface — there is no archive
    endpoint here, only outright deletion — so confirm with the user before
    calling this tool.

    When to Use:
    - Decommissioning a Space that is no longer needed.

    When NOT to Use:
    - To delete a single Folder or List inside the Space (use the folders/lists delete tools).

    Returns:
    A one-line confirmation that the Space was deleted, or an `Error ...` string.

    Examples:
    params = {"space_id": "90130012345"}

    Error Handling:
    404 means the Space is already gone; 401/403 mean the token lacks delete access.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/space/{params.space_id}")
        return f"Deleted Space `{params.space_id}`. This also removed all Folders, Lists, and Tasks within it."
    except Exception as exc:
        return handle_api_error(exc)
