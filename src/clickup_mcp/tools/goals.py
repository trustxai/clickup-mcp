"""Goals & Key Results tools (inventory N — Wave, task t12-goals).

ClickUp Goals are OKR-style containers (`Goal`) holding one or more `Key
Results` ("Targets") that track progress toward the Goal. Key Results are
polymorphic on `type`: `number` / `currency` / `percentage` track a numeric
range (`steps_start` → `steps_end`), `boolean` tracks done/not-done (steps
0/1), and `automatic` derives progress from linked tasks/lists instead of
manual `steps_current` updates.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Context-window guard: independent of any API-side paging (Goals/Key Results
# have none), cap how many goals render in markdown before truncating.
MAX_DISPLAY_GOALS = 50

KeyResultType = Literal["number", "currency", "boolean", "percentage", "automatic"]


def _resolve_team_id(team_id: str | None) -> str:
    """Resolve the Workspace id from the param or CLICKUP_TEAM_ID.

    Mirrors `clickup_mcp.config.Settings.clickup_team_id`'s stated purpose
    ("used by tools when the caller omits team_id"); raises so the caller
    gets a clear, actionable `Error: ...` string instead of a 401/404.
    """
    resolved = team_id or get_settings().clickup_team_id
    if not resolved:
        raise RuntimeError(
            "No team_id provided and CLICKUP_TEAM_ID is not configured. Pass team_id explicitly "
            "or set CLICKUP_TEAM_ID in the environment / .env."
        )
    return resolved


def _as_dict(value: Any) -> dict[str, Any]:
    """Defensively coerce a JSON value to a dict (ClickUp sometimes nests differently)."""
    return value if isinstance(value, dict) else {}


def _format_key_result_line(kr: dict[str, Any]) -> str:
    name = kr.get("name", "Untitled key result")
    kr_id = kr.get("id", "unknown")
    kr_type = kr.get("type", "number")
    steps_current = kr.get("steps_current", 0)
    steps_end = kr.get("steps_end", 0)
    unit = kr.get("unit") or ""
    status = "done" if kr.get("completed") else "in progress"
    progress = f"{steps_current}/{steps_end} {unit}".strip()
    return f"  - **{name}** (`{kr_id}`, {kr_type}, {status}): {progress}"


def _format_goal(goal: dict[str, Any]) -> str:
    name = goal.get("name", "Untitled goal")
    goal_id = goal.get("id", "unknown")
    percent = goal.get("percent_completed", 0)
    due = epoch_to_human(goal.get("due_date"))
    owners = [o for o in (goal.get("owners") or []) if isinstance(o, dict)]
    owner_names = ", ".join(str(o.get("username") or o.get("id", "?")) for o in owners) or "none"
    archived = " (archived)" if goal.get("archived") else ""
    lines = [
        f"## {name}{archived}",
        f"- id: `{goal_id}`",
        f"- progress: {percent}%",
        f"- due: {due}",
        f"- owners: {owner_names}",
    ]
    description = goal.get("description")
    if description:
        lines.append(f"- description: {description}")
    key_results = [kr for kr in (goal.get("key_results") or []) if isinstance(kr, dict)]
    if key_results:
        lines.append(f"- key results ({len(key_results)}):")
        lines.extend(_format_key_result_line(kr) for kr in key_results)
    return "\n".join(lines)


def _format_key_result(kr: dict[str, Any]) -> str:
    name = kr.get("name", "Untitled key result")
    kr_id = kr.get("id", "unknown")
    kr_type = kr.get("type", "number")
    steps_current = kr.get("steps_current", 0)
    steps_end = kr.get("steps_end", 0)
    unit = kr.get("unit") or ""
    status = "done" if kr.get("completed") else "in progress"
    lines = [
        f"**{name}** (`{kr_id}`)",
        f"- goal: `{kr.get('goal_id', 'unknown')}`",
        f"- type: {kr_type} — {status}",
        f"- progress: {steps_current}/{steps_end} {unit}".rstrip(),
    ]
    note = kr.get("last_action", {})
    if isinstance(note, dict) and note.get("note"):
        lines.append(f"- last note: {note['note']}")
    return "\n".join(lines)


class CreateGoalInput(BaseModel):
    """Body for `POST /team/{team_id}/goal`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace id. Defaults to CLICKUP_TEAM_ID when omitted.")
    name: str = Field(min_length=1, description="Goal name.")
    due_date: int | None = Field(default=None, description="Target date as a unix epoch in milliseconds.")
    description: str | None = Field(default=None, description="Free-text goal description.")
    multiple_owners: bool = Field(default=True, description="Whether the Goal can have more than one owner.")
    owners: list[int] | None = Field(default=None, description="User ids to set as Goal owners.")
    color: str | None = Field(default=None, description="Hex color for the Goal, e.g. `#32a852`.")


class GetGoalsInput(BaseModel):
    """Query for `GET /team/{team_id}/goal`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(default=None, description="Workspace id. Defaults to CLICKUP_TEAM_ID when omitted.")
    include_completed: bool = Field(default=False, description="Include Goals that are already completed.")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class GetGoalInput(BaseModel):
    """Path for `GET /goal/{goal_id}`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    goal_id: str = Field(min_length=1, description="Goal id (UUID).")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format.")


class UpdateGoalInput(BaseModel):
    """Body for `PUT /goal/{goal_id}`. At least one updatable field is required."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    goal_id: str = Field(min_length=1, description="Goal id (UUID) to update.")
    name: str | None = Field(default=None, description="New Goal name.")
    due_date: int | None = Field(default=None, description="New target date, unix epoch milliseconds.")
    description: str | None = Field(default=None, description="New free-text description.")
    add_owners: list[int] | None = Field(default=None, description="User ids to add as owners.")
    rem_owners: list[int] | None = Field(default=None, description="User ids to remove as owners.")
    color: str | None = Field(default=None, description="New hex color, e.g. `#32a852`.")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateGoalInput:
        updatable = (self.name, self.due_date, self.description, self.add_owners, self.rem_owners, self.color)
        if all(field is None for field in updatable):
            raise ValueError(
                "Provide at least one field to update (name, due_date, description, add_owners, rem_owners, color)."
            )
        return self


class DeleteGoalInput(BaseModel):
    """Path for `DELETE /goal/{goal_id}`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    goal_id: str = Field(min_length=1, description="Goal id (UUID) to permanently delete.")


class CreateKeyResultInput(BaseModel):
    """Body for `POST /goal/{goal_id}/key_result`.

    `type` selects how progress is tracked: `number`/`currency`/`percentage`
    move `steps_current` between `steps_start` and `steps_end`; `boolean` is
    an on/off target (steps 0/1); `automatic` derives progress from the
    linked `task_ids`/`list_ids` instead of manual steps.

    For `type="boolean"`, `steps_start`/`steps_end` are coerced to `0`/`1`
    when left at their defaults — a boolean key result only ever has two
    states. Explicitly passing a `steps_end` other than `1` for a boolean
    key result is rejected rather than silently accepted.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    goal_id: str = Field(min_length=1, description="Goal id (UUID) this key result belongs to.")
    name: str = Field(min_length=1, description="Key result name.")
    type: KeyResultType = Field(description="One of number, currency, boolean, percentage, automatic.")
    owners: list[int] = Field(default_factory=list, description="User ids responsible for this key result.")
    steps_start: int = Field(default=0, description="Starting value of the progress range.")
    steps_end: int = Field(
        default=100,
        description="Target value of the progress range. Ignored/coerced to 1 for type='boolean' "
        "(boolean key results always use a 0/1 range) unless you explicitly pass a non-1 value, "
        "which raises a validation error.",
    )
    unit: str | None = Field(default=None, description="Display unit, e.g. `%`, `$`, `tasks`.")
    task_ids: list[str] | None = Field(default=None, description="Task ids to link for automatic progress tracking.")
    list_ids: list[str] | None = Field(default=None, description="List ids to link for automatic progress tracking.")

    @model_validator(mode="after")
    def _validate_type_specific_fields(self) -> CreateKeyResultInput:
        if self.type == "automatic" and not self.task_ids and not self.list_ids:
            raise ValueError("type='automatic' requires task_ids and/or list_ids to derive progress from.")
        if self.type == "boolean":
            end_explicit = "steps_end" in self.model_fields_set
            if end_explicit and self.steps_end != 1:
                raise ValueError(
                    "type='boolean' key results use a fixed 0/1 progress range "
                    f"(got steps_end={self.steps_end}); omit steps_start/steps_end to use the default 0/1."
                )
            if "steps_start" not in self.model_fields_set:
                self.steps_start = 0
            if not end_explicit:
                self.steps_end = 1
        return self


class EditKeyResultInput(BaseModel):
    """Body for `PUT /key_result/{key_result_id}`. At least one updatable field is required."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    key_result_id: str = Field(min_length=1, description="Key result id (UUID) to edit.")
    steps_current: int | None = Field(default=None, description="New current progress value.")
    note: str | None = Field(default=None, description="Progress note/comment attached to this update.")
    name: str | None = Field(default=None, description="New key result name.")
    owners: list[int] | None = Field(default=None, description="Full replacement list of owner user ids.")
    task_ids: list[str] | None = Field(default=None, description="Replacement linked task ids (automatic type).")
    list_ids: list[str] | None = Field(default=None, description="Replacement linked list ids (automatic type).")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> EditKeyResultInput:
        updatable = (self.steps_current, self.note, self.name, self.owners, self.task_ids, self.list_ids)
        if all(field is None for field in updatable):
            raise ValueError(
                "Provide at least one field to update (steps_current, note, name, owners, task_ids, list_ids)."
            )
        return self


class DeleteKeyResultInput(BaseModel):
    """Path for `DELETE /key_result/{key_result_id}`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    key_result_id: str = Field(min_length=1, description="Key result id (UUID) to permanently delete.")


@mcp.tool(
    name="clickup_create_goal",
    annotations=ToolAnnotations(
        title="Create Goal",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_goal(params: CreateGoalInput) -> str:
    """Create a new Goal in a Workspace.

    Goals group one or more Key Results (see `clickup_create_key_result`)
    that track progress toward a target date.

    When to Use:
    - Starting a new OKR / objective that will hold Key Results.

    When NOT to Use:
    - To add a Key Result to an existing Goal — use `clickup_create_key_result`.
    - To change an existing Goal's fields — use `clickup_update_goal`.

    Returns:
    A confirmation string with the new Goal's id and current fields.

    Examples:
    params = {"name": "Grow MRR", "due_date": 1735689600000, "color": "#32a852"}

    Error Handling:
    404 usually means the team_id is wrong; 401 means the token is missing/invalid.
    """
    try:
        team = _resolve_team_id(params.team_id)
        client = get_client()
        body: dict[str, Any] = {"name": params.name, "multiple_owners": params.multiple_owners}
        if params.due_date is not None:
            body["due_date"] = params.due_date
        if params.description is not None:
            body["description"] = params.description
        if params.owners is not None:
            body["owners"] = params.owners
        if params.color is not None:
            body["color"] = params.color
        resp = await client.request("POST", f"/team/{team}/goal", json_body=body)
        goal = _as_dict(resp.json().get("goal"))
        return f"Created goal (id `{goal.get('id', 'unknown')}`).\n\n{_format_goal(goal)}"
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_goals",
    annotations=ToolAnnotations(
        title="Get Goals",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_goals(params: GetGoalsInput) -> str:
    """List the Goals in a Workspace, including any Goal Folders.

    When to Use:
    - Browsing all OKRs for a Workspace, or checking which are still open.

    When NOT to Use:
    - Fetching one Goal's full detail (including Key Results) — use `clickup_get_goal`.

    Returns:
    Markdown (default) or JSON listing every Goal, including Goals nested
    inside Goal Folders. Each Goal's Key Results are summarized inline.

    Pagination:
    This endpoint does not page server-side; display is capped at
    MAX_DISPLAY_GOALS (50) with a truncation note if exceeded. Use
    `include_completed=false` (default) to shrink the result set.

    Examples:
    params = {"include_completed": True, "response_format": "json"}

    Error Handling:
    404 usually means the team_id is wrong; 401 means the token is missing/invalid.
    """
    try:
        team = _resolve_team_id(params.team_id)
        client = get_client()
        query = {"include_completed": str(params.include_completed).lower()}
        resp = await client.request("GET", f"/team/{team}/goal", params=query)
        data = resp.json()
        goals = [g for g in (data.get("goals") or []) if isinstance(g, dict)]
        folders = [f for f in (data.get("folders") or []) if isinstance(f, dict)]
        folder_goals = [g for f in folders for g in (f.get("goals") or []) if isinstance(g, dict)]
        all_goals = goals + folder_goals

        if params.response_format is ResponseFormat.JSON:
            return to_json({"goals": goals, "folders": folders, "count": len(all_goals)})

        total = len(all_goals)
        shown = all_goals[:MAX_DISPLAY_GOALS]
        lines = [f"# Goals ({total})", ""]
        if not shown:
            lines.append("_No goals found._")
        for goal in shown:
            lines.append(_format_goal(goal))
            lines.append("")
        if total > len(shown):
            lines.append(f"_Truncated to {MAX_DISPLAY_GOALS} of {total} goals; narrow with include_completed=false._")
        return "\n".join(lines)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_goal",
    annotations=ToolAnnotations(
        title="Get Goal",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_goal(params: GetGoalInput) -> str:
    """Get a single Goal's full detail, including its Key Results.

    When to Use:
    - Inspecting one Goal's progress and Key Results before editing it.

    When NOT to Use:
    - Listing many Goals at once — use `clickup_get_goals`.

    Returns:
    Markdown (default) or JSON with the Goal's fields and every Key Result.

    Examples:
    params = {"goal_id": "e53a033c-1146-4b58-b498-7ec39b5661c2"}

    Error Handling:
    404 means the goal_id does not exist or was already deleted.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/goal/{params.goal_id}")
        goal = _as_dict(resp.json().get("goal"))
        if params.response_format is ResponseFormat.JSON:
            return to_json(goal)
        return _format_goal(goal)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_goal",
    annotations=ToolAnnotations(
        title="Update Goal",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_goal(params: UpdateGoalInput) -> str:
    """Update an existing Goal's name, due date, description, owners, or color.

    Owners are changed incrementally via `add_owners`/`rem_owners` rather
    than a full replacement list.

    When to Use:
    - Renaming a Goal, pushing its due date, or changing who owns it.

    When NOT to Use:
    - Editing a Key Result's progress — use `clickup_edit_key_result`.

    Returns:
    A confirmation string with the Goal's fields after the update.

    Examples:
    params = {"goal_id": "e53a033c-1146-4b58-b498-7ec39b5661c2", "add_owners": [123]}

    Error Handling:
    404 means the goal_id does not exist; 400 means a field value was rejected
    (e.g. malformed color or due_date).
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.due_date is not None:
            body["due_date"] = params.due_date
        if params.description is not None:
            body["description"] = params.description
        if params.add_owners is not None:
            body["add_owners"] = params.add_owners
        if params.rem_owners is not None:
            body["rem_owners"] = params.rem_owners
        if params.color is not None:
            body["color"] = params.color
        resp = await client.request("PUT", f"/goal/{params.goal_id}", json_body=body)
        goal = _as_dict(resp.json().get("goal"))
        return f"Updated goal `{params.goal_id}`.\n\n{_format_goal(goal)}"
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_goal",
    annotations=ToolAnnotations(
        title="Delete Goal",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_goal(params: DeleteGoalInput) -> str:
    """Permanently delete a Goal and all of its Key Results.

    When to Use:
    - Removing an OKR that is no longer tracked.

    When NOT to Use:
    - Removing a single Key Result but keeping the Goal — use `clickup_delete_key_result`.

    Returns:
    A confirmation string on success.

    Error Handling:
    404 means the goal_id does not exist or was already deleted.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/goal/{params.goal_id}")
        return f"Deleted goal `{params.goal_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_key_result",
    annotations=ToolAnnotations(
        title="Create Key Result",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_key_result(params: CreateKeyResultInput) -> str:
    """Add a Key Result (Target) to a Goal.

    `type` determines how progress is tracked — see the module docstring.
    `automatic` requires `task_ids` and/or `list_ids` since progress is
    derived from those tasks/lists rather than manual `steps_current` edits.

    When to Use:
    - Breaking a Goal down into one or more measurable targets.

    When NOT to Use:
    - Updating an existing Key Result's progress — use `clickup_edit_key_result`.

    Returns:
    A confirmation string with the new Key Result's id and fields.

    Examples:
    params = {
        "goal_id": "e53a033c-1146-4b58-b498-7ec39b5661c2",
        "name": "Close 10 deals",
        "type": "number",
        "steps_start": 0,
        "steps_end": 10,
        "unit": "deals",
        "owners": [123],
    }

    Error Handling:
    404 means the goal_id does not exist; 400 means the type-specific fields
    (steps_start/steps_end/task_ids/list_ids) are inconsistent for the chosen type.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {
            "name": params.name,
            "type": params.type,
            "owners": params.owners,
            "steps_start": params.steps_start,
            "steps_end": params.steps_end,
        }
        if params.unit is not None:
            body["unit"] = params.unit
        if params.task_ids is not None:
            body["task_ids"] = params.task_ids
        if params.list_ids is not None:
            body["list_ids"] = params.list_ids
        resp = await client.request("POST", f"/goal/{params.goal_id}/key_result", json_body=body)
        kr = _as_dict(resp.json().get("key_result"))
        return f"Created key result (id `{kr.get('id', 'unknown')}`).\n\n{_format_key_result(kr)}"
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_edit_key_result",
    annotations=ToolAnnotations(
        title="Edit Key Result",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_key_result(params: EditKeyResultInput) -> str:
    """Update a Key Result's progress, note, name, owners, or task/list links.

    `steps_current` moves progress along the range set by `create_key_result`
    (`steps_start`→`steps_end`); `note` attaches a short progress comment
    alongside the update.

    When to Use:
    - Logging progress on a manual (number/currency/boolean/percentage) Key Result.

    When NOT to Use:
    - Creating a brand-new Key Result — use `clickup_create_key_result`.
    - Editing the parent Goal's own fields — use `clickup_update_goal`.

    Returns:
    A confirmation string with the Key Result's fields after the update.

    Examples:
    params = {
        "key_result_id": "8480-49bc-8c57-e569747efe93",
        "steps_current": 4,
        "note": "Closed another deal this week.",
    }

    Error Handling:
    404 means the key_result_id does not exist; 400 means steps_current is
    outside the type's valid range.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {}
        if params.steps_current is not None:
            body["steps_current"] = params.steps_current
        if params.note is not None:
            body["note"] = params.note
        if params.name is not None:
            body["name"] = params.name
        if params.owners is not None:
            body["owners"] = params.owners
        if params.task_ids is not None:
            body["task_ids"] = params.task_ids
        if params.list_ids is not None:
            body["list_ids"] = params.list_ids
        resp = await client.request("PUT", f"/key_result/{params.key_result_id}", json_body=body)
        kr = _as_dict(resp.json().get("key_result"))
        return f"Updated key result `{params.key_result_id}`.\n\n{_format_key_result(kr)}"
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_key_result",
    annotations=ToolAnnotations(
        title="Delete Key Result",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_key_result(params: DeleteKeyResultInput) -> str:
    """Permanently delete a Key Result (Target) from its Goal.

    When to Use:
    - Removing a target that no longer applies, while keeping the parent Goal.

    When NOT to Use:
    - Deleting the whole Goal (and all its Key Results) — use `clickup_delete_goal`.

    Returns:
    A confirmation string on success.

    Error Handling:
    404 means the key_result_id does not exist or was already deleted.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/key_result/{params.key_result_id}")
        return f"Deleted key result `{params.key_result_id}`."
    except Exception as exc:
        return handle_api_error(exc)
