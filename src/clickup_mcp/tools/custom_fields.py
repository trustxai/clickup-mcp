"""Custom Fields tools — read field definitions at four scopes, set/remove a
field's value on a task, and list the Workspace's custom task types.

The interesting surface here is :func:`clickup_set_custom_field_value`, whose
request body is *polymorphic*: the shape of ``value`` depends entirely on the
Custom Field's ``type``. The value table in that tool's docstring is the
load-bearing reference — read it before setting a value.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, to_json
from clickup_mcp.server import mcp

# Display caps keep large field/option catalogues inside the MCP response budget;
# they are independent of the API (these endpoints are not paginated).
MAX_DISPLAY_FIELDS = 100
MAX_DISPLAY_OPTIONS = 30


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _include_applied_params(include_applied_objects: bool) -> dict[str, Any] | None:
    """Build the query for the field-catalogue endpoints (or ``None``)."""
    return {"include_applied_objects": "true"} if include_applied_objects else None


def _custom_task_id_params(custom_task_ids: bool, team_id: str | None) -> dict[str, Any] | None:
    """Build the ``custom_task_ids``/``team_id`` query pair for task-value endpoints.

    ClickUp only honours ``team_id`` when ``custom_task_ids=true`` (it disambiguates
    a Custom Task ID, which is unique per Workspace not globally).
    """
    if not custom_task_ids:
        return None
    query: dict[str, Any] = {"custom_task_ids": "true"}
    if team_id:
        query["team_id"] = team_id
    return query


def _format_field(field: dict[str, Any]) -> str:
    """Render one Custom Field definition as a markdown bullet."""
    name = field.get("name") or "(unnamed)"
    ftype = field.get("type") or "unknown"
    fid = field.get("id") or "?"
    header = f"- **{name}** — type `{ftype}`, id `{fid}`"
    if field.get("required"):
        header += "  _(required)_"
    lines = [header]

    type_config = field.get("type_config") or {}
    options = type_config.get("options")
    if isinstance(options, list) and options:
        rendered = ", ".join(
            f"{opt.get('name') or opt.get('label') or '(option)'} (`{opt.get('id')}`)"
            for opt in options[:MAX_DISPLAY_OPTIONS]
        )
        lines.append(f"    options: {rendered}")
        if len(options) > MAX_DISPLAY_OPTIONS:
            lines.append(f"    …and {len(options) - MAX_DISPLAY_OPTIONS:,} more option(s).")
    return "\n".join(lines)


def _format_fields_response(*, fields: list[dict[str, Any]], fmt: ResponseFormat, title: str) -> str:
    """Uniform output for the four field-catalogue tools."""
    if fmt is ResponseFormat.JSON:
        return to_json({"title": title, "count": len(fields), "fields": fields})

    lines = [f"# {title}", "", f"**{len(fields):,}** Custom Field(s) accessible at this scope.", ""]
    if not fields:
        lines.append("_No Custom Fields accessible here._")
        return "\n".join(lines)
    for field in fields[:MAX_DISPLAY_FIELDS]:
        lines.append(_format_field(field))
    if len(fields) > MAX_DISPLAY_FIELDS:
        lines.append("")
        lines.append(
            f"_Showing first {MAX_DISPLAY_FIELDS} of {len(fields):,} fields — narrow the scope to see the rest._"
        )
    return "\n".join(lines)


def _format_custom_item(item: dict[str, Any]) -> str:
    """Render one custom task type as a markdown bullet."""
    name = item.get("name") or "(unnamed)"
    item_id = item.get("id")
    header = f"- **{name}** — id `{item_id}`"
    plural = item.get("name_plural")
    if plural:
        header += f" (plural: {plural})"
    description = item.get("description")
    if description:
        header += f" — {description}"
    return header


# --------------------------------------------------------------------------- #
# Field catalogue (read) — one tool per scope
# --------------------------------------------------------------------------- #
class _FieldScopeBase(BaseModel):
    """Shared surface for the four field-catalogue tools."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    include_applied_objects: bool = Field(
        default=False,
        description=(
            "When true, include the `applied_objects` array on task-type-scoped Custom Fields "
            "(each entry is {object_type: 19, object_id: <custom-item-id>}). Defaults to false."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` (default, human-readable) or `json` (raw field objects incl. full type_config).",
    )


class GetListCustomFieldsInput(_FieldScopeBase):
    list_id: str = Field(..., min_length=1, description="ID of the List whose accessible Custom Fields to return.")


class GetFolderCustomFieldsInput(_FieldScopeBase):
    folder_id: str = Field(..., min_length=1, description="ID of the Folder whose accessible Custom Fields to return.")


class GetSpaceCustomFieldsInput(_FieldScopeBase):
    space_id: str = Field(..., min_length=1, description="ID of the Space whose accessible Custom Fields to return.")


class GetTeamCustomFieldsInput(_FieldScopeBase):
    team_id: str = Field(
        ...,
        min_length=1,
        description="Workspace ID (ClickUp calls it `team_id`) whose Workspace-scoped Custom Fields to return.",
    )


@mcp.tool(
    name="clickup_get_list_custom_fields",
    annotations=ToolAnnotations(
        title="Get List Custom Fields",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_list_custom_fields(params: GetListCustomFieldsInput) -> str:
    """List the Custom Fields accessible from a List.

    Calls `GET /list/{list_id}/field`. This is the scope you almost always want
    before setting a value: it returns every field usable on the List's tasks —
    fields defined on the List plus those inherited from the parent Folder,
    Space, and Workspace. Each field object carries the `id` (a UUID you pass to
    `clickup_set_custom_field_value`), `type`, and `type_config` (for drop_down /
    labels fields, `type_config.options[]` holds the option UUIDs you set).

    When to Use:
    - To discover a field's `id` and its option UUIDs before calling
      `clickup_set_custom_field_value` on a task in this List.

    When NOT to Use:
    - For fields defined higher up only: prefer the narrowest scope that includes
      your task. Use `clickup_get_folder_custom_fields`,
      `clickup_get_space_custom_fields`, or `clickup_get_team_custom_fields` to
      inspect a specific level.

    Returns:
    A markdown list of fields (name, type, id, and any options) or, with
    `response_format="json"`, the raw field objects including full `type_config`.

    Examples:
    - params = {"list_id": "901100154842"}
    - params = {"list_id": "901100154842", "include_applied_objects": true, "response_format": "json"}

    Error Handling:
    404 means the List id is wrong; 401/403 point to token or access issues.
    """
    try:
        client = get_client()
        resp = await client.request(
            "GET",
            f"/list/{params.list_id}/field",
            params=_include_applied_params(params.include_applied_objects),
        )
        fields = resp.json().get("fields", [])
        return _format_fields_response(fields=fields, fmt=params.response_format, title="List Custom Fields")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_folder_custom_fields",
    annotations=ToolAnnotations(
        title="Get Folder Custom Fields",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_folder_custom_fields(params: GetFolderCustomFieldsInput) -> str:
    """List the Custom Fields available at a Folder scope.

    Calls `GET /folder/{folder_id}/field`, returning fields defined on the Folder
    plus those inherited from the parent Space and Workspace.

    When to Use:
    - To audit which fields a Folder exposes to its Lists and tasks.

    When NOT to Use:
    - To find a field usable on a specific task — use
      `clickup_get_list_custom_fields` for the narrowest, task-applicable set.

    Returns:
    A markdown list of fields (or raw JSON with `response_format="json"`).

    Examples:
    - params = {"folder_id": "457"}

    Error Handling:
    404 means the Folder id is wrong; 401/403 point to token or access issues.
    """
    try:
        client = get_client()
        resp = await client.request(
            "GET",
            f"/folder/{params.folder_id}/field",
            params=_include_applied_params(params.include_applied_objects),
        )
        fields = resp.json().get("fields", [])
        return _format_fields_response(fields=fields, fmt=params.response_format, title="Folder Custom Fields")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_space_custom_fields",
    annotations=ToolAnnotations(
        title="Get Space Custom Fields",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_space_custom_fields(params: GetSpaceCustomFieldsInput) -> str:
    """List the Custom Fields available at a Space scope.

    Calls `GET /space/{space_id}/field`, returning fields defined on the Space
    plus those inherited from the Workspace.

    When to Use:
    - To audit which fields a Space exposes to its Folders, Lists, and tasks.

    When NOT to Use:
    - To find a field usable on a specific task — use
      `clickup_get_list_custom_fields`.

    Returns:
    A markdown list of fields (or raw JSON with `response_format="json"`).

    Examples:
    - params = {"space_id": "790"}

    Error Handling:
    404 means the Space id is wrong; 401/403 point to token or access issues.
    """
    try:
        client = get_client()
        resp = await client.request(
            "GET",
            f"/space/{params.space_id}/field",
            params=_include_applied_params(params.include_applied_objects),
        )
        fields = resp.json().get("fields", [])
        return _format_fields_response(fields=fields, fmt=params.response_format, title="Space Custom Fields")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_team_custom_fields",
    annotations=ToolAnnotations(
        title="Get Workspace Custom Fields",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_team_custom_fields(params: GetTeamCustomFieldsInput) -> str:
    """List the Workspace-scoped Custom Fields.

    Calls `GET /team/{team_id}/field` (ClickUp names the Workspace `team_id`),
    returning only fields defined at the top Workspace level.

    When to Use:
    - To inventory the organisation-wide Custom Fields shared across every Space.

    When NOT to Use:
    - To find a field usable on a specific task — use
      `clickup_get_list_custom_fields`, which also surfaces inherited fields.

    Returns:
    A markdown list of fields (or raw JSON with `response_format="json"`).

    Examples:
    - params = {"team_id": "9007200144"}

    Error Handling:
    404 means the Workspace id is wrong; 401/403 point to token or access issues.
    """
    try:
        client = get_client()
        resp = await client.request(
            "GET",
            f"/team/{params.team_id}/field",
            params=_include_applied_params(params.include_applied_objects),
        )
        fields = resp.json().get("fields", [])
        return _format_fields_response(fields=fields, fmt=params.response_format, title="Workspace Custom Fields")
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Set / remove a field value on a task (polymorphic body)
# --------------------------------------------------------------------------- #
class SetCustomFieldValueInput(BaseModel):
    """Body + query for `POST /task/{task_id}/field/{field_id}`.

    `value` is intentionally typed as JSON `Any` because its shape is dictated by
    the target field's type — see the tool docstring's value table.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task to update (or Custom Task ID — see below).")
    field_id: str = Field(..., min_length=1, description="UUID of the Custom Field to set (from a get-fields tool).")
    value: Any = Field(
        ...,
        description=(
            "The field value — shape depends on the field TYPE. Scalar (text/number/currency/date/rating), "
            "an option UUID string (drop_down), an array of option UUIDs (labels), or an object "
            "({add,rem} for tasks/people; {location,...} for location). See the value table in the tool docstring."
        ),
    )
    value_options: dict[str, Any] | None = Field(
        default=None,
        description='Extra options for the value; for date fields pass {"time": true} to keep the time-of-day.',
    )
    custom_task_ids: bool = Field(
        default=False,
        description="Set true to treat `task_id` as a Custom Task ID; then `team_id` is required.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace ID — required only when `custom_task_ids=true`.",
    )


class RemoveCustomFieldValueInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., min_length=1, description="ID of the task whose field value to clear.")
    field_id: str = Field(..., min_length=1, description="UUID of the Custom Field to clear on the task.")
    custom_task_ids: bool = Field(
        default=False,
        description="Set true to treat `task_id` as a Custom Task ID; then `team_id` is required.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace ID — required only when `custom_task_ids=true`.",
    )


@mcp.tool(
    name="clickup_set_custom_field_value",
    annotations=ToolAnnotations(
        title="Set Custom Field Value",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_set_custom_field_value(params: SetCustomFieldValueInput) -> str:
    """Set a Custom Field's value on a task.

    Calls `POST /task/{task_id}/field/{field_id}`. The request body is
    POLYMORPHIC — the shape of `value` is decided by the field's `type`, which
    you can read with `clickup_get_list_custom_fields`. Send the wrong shape and
    ClickUp answers 400. drop_down / labels take the option UUIDs found in the
    field's `type_config.options[]`, not the display labels.

    Value by field type:

    | Field type(s)                          | `value` shape                                   | Example |
    | -------------------------------------- | ----------------------------------------------- | ------- |
    | text, short_text, url, email, phone    | JSON string                                     | `"Ada Lovelace"` |
    | number, currency, money                | JSON number                                     | `8000` |
    | date                                   | int64 **unix milliseconds**; pair with `value_options={"time": true}` to keep the time-of-day (else date-only) | `1667367645000` |
    | checkbox                               | boolean                                         | `true` |
    | emoji / rating                         | integer (count of filled icons)                 | `4` |
    | drop_down (single-select)              | a single option **UUID string**                 | `"03efda77-c7a0-42d3-8afd-fd546353c2f5"` |
    | labels (multi-select)                  | an **array of option UUID strings** (the full desired set)  | `["uuidA", "uuidB"]` |
    | tasks (task relationship)              | object `{"add": [task_id...], "rem": [task_id...]}` | `{"add": ["abcd1234"], "rem": ["jklm9876"]}` |
    | users (people)                         | object `{"add": [user_id...], "rem": [user_id...]}` | `{"add": [123, 456], "rem": [987]}` |
    | location                               | object `{"location": {"lat": <num>, "lng": <num>}, "formatted_address": <str>}` (a Google `place_id` may be given instead of/with lat-lng) | see below |

    Note: labels differ from tasks/people — labels take a plain array of UUIDs
    (the complete desired selection), NOT an `{add, rem}` object; `{add, rem}` is
    only for the tasks-relationship and people field types.

    When to Use:
    - To populate or overwrite a single Custom Field on one task after resolving
      its `field_id` (and any option UUIDs) via `clickup_get_list_custom_fields`.

    When NOT to Use:
    - To clear a field — use `clickup_remove_custom_field_value` instead of
      sending an empty value.
    - To set many fields at once on create — pass the `custom_fields` array to the
      task create/update tools in the Tasks module instead.

    Returns:
    A confirmation string (the endpoint returns an empty body on success).

    Examples:
    - text:      params = {"task_id": "9hz", "field_id": "<uuid>", "value": "In review"}
    - number:    params = {"task_id": "9hz", "field_id": "<uuid>", "value": 42}
    - date+time: params = {"task_id": "9hz", "field_id": "<uuid>", "value": 1667367645000, "value_options": {"time": true}}
    - drop_down: params = {"task_id": "9hz", "field_id": "<uuid>", "value": "03efda77-c7a0-42d3-8afd-fd546353c2f5"}
    - labels:    params = {"task_id": "9hz", "field_id": "<uuid>", "value": ["uuidA", "uuidB"]}
    - tasks:     params = {"task_id": "9hz", "field_id": "<uuid>", "value": {"add": ["abcd1234"], "rem": []}}
    - location:  params = {"task_id": "9hz", "field_id": "<uuid>", "value": {"location": {"lat": -28.016, "lng": 153.4}, "formatted_address": "Gold Coast QLD, Australia"}}
    - custom id: params = {"task_id": "ABC-123", "field_id": "<uuid>", "value": 42, "custom_task_ids": true, "team_id": "9007200144"}

    Error Handling:
    400 usually means the `value` shape does not match the field's type, or the
    field is not enabled for the task's custom task type; 404 means the task or
    `field_id` is wrong; if `custom_task_ids=true` you must also pass `team_id`.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"value": params.value}
        if params.value_options is not None:
            body["value_options"] = params.value_options
        await client.request(
            "POST",
            f"/task/{params.task_id}/field/{params.field_id}",
            params=_custom_task_id_params(params.custom_task_ids, params.team_id),
            json_body=body,
        )
        return f"Set Custom Field `{params.field_id}` on task `{params.task_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_remove_custom_field_value",
    annotations=ToolAnnotations(
        title="Remove Custom Field Value",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_remove_custom_field_value(params: RemoveCustomFieldValueInput) -> str:
    """Clear a Custom Field's value on a task.

    Calls `DELETE /task/{task_id}/field/{field_id}`. This removes the value
    stored on the task only — it does NOT delete the field definition or any
    drop_down/labels options from the field's configuration.

    When to Use:
    - To empty a single field on a task (e.g. unset a drop_down or clear a date).

    When NOT to Use:
    - To change a value — use `clickup_set_custom_field_value`.
    - To delete the field itself — that is a ClickApp/UI action, not this API.

    Returns:
    A confirmation string (the endpoint returns an empty body on success).

    Examples:
    - params = {"task_id": "9hz", "field_id": "<uuid>"}
    - params = {"task_id": "ABC-123", "field_id": "<uuid>", "custom_task_ids": true, "team_id": "9007200144"}

    Error Handling:
    404 means the task or `field_id` is wrong; if `custom_task_ids=true` you must
    also pass `team_id`.
    """
    try:
        client = get_client()
        await client.request(
            "DELETE",
            f"/task/{params.task_id}/field/{params.field_id}",
            params=_custom_task_id_params(params.custom_task_ids, params.team_id),
        )
        return f"Cleared Custom Field `{params.field_id}` on task `{params.task_id}`."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Custom task types (custom items)
# --------------------------------------------------------------------------- #
class GetCustomTaskTypesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str = Field(
        ...,
        min_length=1,
        description="Workspace ID (ClickUp calls it `team_id`) whose custom task types to return.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` (default) or `json` (raw custom_item objects).",
    )


@mcp.tool(
    name="clickup_get_custom_task_types",
    annotations=ToolAnnotations(
        title="Get Custom Task Types",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_custom_task_types(params: GetCustomTaskTypesInput) -> str:
    """List the Workspace's custom task types (a.k.a. custom items).

    Calls `GET /team/{team_id}/custom_item`. Custom task types are the "task /
    milestone / bug / …" varieties a Workspace defines; their integer `id` is the
    `custom_item_id` used when creating tasks and is what a task-type-scoped
    Custom Field's `applied_objects[].object_id` points at. The default "Task"
    type is `id 0` and is not returned by this endpoint.

    When to Use:
    - To resolve a custom task type name to its `id` before creating a typed task.
    - To interpret `applied_objects` from the get-fields tools (`include_applied_objects=true`).

    When NOT to Use:
    - To read Custom Fields — use the get-fields tools above.

    Returns:
    A markdown list of custom task types (id, name, plural, description) or, with
    `response_format="json"`, the raw `custom_item` objects.

    Examples:
    - params = {"team_id": "9007200144"}
    - params = {"team_id": "9007200144", "response_format": "json"}

    Error Handling:
    404 means the Workspace id is wrong; 401/403 point to token or access issues.
    """
    try:
        client = get_client()
        resp = await client.request("GET", f"/team/{params.team_id}/custom_item")
        items = resp.json().get("custom_items", [])
        if params.response_format is ResponseFormat.JSON:
            return to_json({"title": "Custom Task Types", "count": len(items), "custom_items": items})
        lines = [
            "# Custom Task Types",
            "",
            f"**{len(items):,}** custom task type(s) (the default `Task` type, id 0, is not listed).",
            "",
        ]
        if not items:
            lines.append("_No custom task types defined in this Workspace._")
            return "\n".join(lines)
        for item in items:
            lines.append(_format_custom_item(item))
        return "\n".join(lines)
    except Exception as exc:
        return handle_api_error(exc)
