"""ClickUp Workspace-administration tools (plan, seats, audit logs, ACLs).

Two v2 read tools report on a Workspace's subscription:
``clickup_get_workspace_plan`` and ``clickup_get_workspace_seats``.

Two v3 tools cover Enterprise administration:
``clickup_query_audit_logs`` (owner-only audit trail) and
``clickup_update_privacy_and_access`` (share/unshare any object). Both live on
the ``/api/v3/workspaces/{workspace_id}/...`` surface (snake_case path segments)
and are gated to the Enterprise plan.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Output byte guard, independent of API paging, to stay under the 1 MB MCP limit.
MAX_OUTPUT_CHARS = 800_000

# Permission levels accepted by the ACL patch endpoint (null = remove access).
_PERMISSION_LABELS: dict[int | None, str] = {
    1: "read",
    3: "comment",
    4: "edit",
    5: "create",
    None: "remove access",
}


class AuditApplicability(StrEnum):
    """Which class of audit events to query (required top-level filter)."""

    AUTH_AND_SECURITY = "auth-and-security"
    CUSTOM_FIELDS = "custom-fields"
    HIERARCHY_ACTIVITY = "hierarchy-activity"
    USER_ACTIVITY = "user-activity"
    AGENT_SETTINGS_ACTIVITY = "agent-settings-activity"
    OTHER_ACTIVITY = "other-activity"


class AuditEventStatus(StrEnum):
    """Outcome filter for audit events."""

    SUCCESS = "success"
    FAILED = "failed"
    WARN = "warn"
    SKIPPED = "skipped"
    STARTED = "started"
    COMPLETED = "completed"
    ERROR = "error"
    SYSTEM_ERROR = "system_error"


def _resolve_team_id(value: str | None) -> str:
    """Return the caller's Workspace id (a.k.a. team id), else the default.

    In the ClickUp API the Workspace id and the ``team_id`` path parameter are
    the same value; v2 endpoints call it ``team_id`` and v3 calls it
    ``workspace_id``.
    """
    resolved = (value or "").strip() or get_settings().clickup_team_id.strip()
    if not resolved:
        raise ValueError(
            "No Workspace id available. Pass the Workspace/team id, or set CLICKUP_TEAM_ID in the environment or .env."
        )
    return resolved


def _cap(text: str) -> str:
    """Trim oversized output so a single response cannot blow the MCP size limit."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n\n…output truncated (response exceeded display cap)."


def _extract_audit_entries(payload: Any) -> list[dict[str, Any]]:
    """Normalize the audit-log response (keyed object vs bare array)."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("logs") or payload.get("items") or []
        return [row for row in rows if isinstance(row, dict)]
    return []


def _format_audit_entry(entry: dict[str, Any]) -> str:
    event = entry.get("eventType") or entry.get("event") or "(event)"
    status = entry.get("eventStatus") or entry.get("status") or "?"
    ts = entry.get("timestamp") or entry.get("date") or entry.get("createdAt")
    user = entry.get("userEmail") or entry.get("userId")
    if user is None and isinstance(entry.get("user"), dict):
        user = entry["user"].get("email") or entry["user"].get("id")
    parts = [f"- **{event}** [{status}]"]
    if user is not None:
        parts.append(f" by `{user}`")
    if ts is not None:
        parts.append(f" at {epoch_to_human(ts)}")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class GetWorkspacePlanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(
        default=None,
        description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class GetWorkspaceSeatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(
        default=None,
        description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class QueryAuditLogsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    applicability: AuditApplicability = Field(
        description=(
            "Required. Which class of events to return: auth-and-security, custom-fields, "
            "hierarchy-activity, user-activity, agent-settings-activity, or other-activity."
        )
    )
    workspace_id: str | None = Field(
        default=None, description="Workspace id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    user_ids: list[str] | None = Field(default=None, description="Filter to these user ids.")
    user_emails: list[str] | None = Field(default=None, description="Filter to these user emails.")
    event_types: list[str] | None = Field(
        default=None,
        description="Filter to these event type names (e.g. ['USER_LOGIN', 'CHANGE_PASSWORD', 'TASK_CREATED']).",
    )
    event_status: AuditEventStatus | None = Field(
        default=None,
        description="Filter by outcome: success, failed, warn, skipped, started, completed, error, system_error.",
    )
    start_time: int | None = Field(default=None, description="Window start as a unix epoch in milliseconds.")
    end_time: int | None = Field(default=None, description="Window end as a unix epoch in milliseconds.")
    page_rows: int = Field(default=25, ge=1, le=100, description="Rows to return per page (1–100).")
    page_timestamp: int | None = Field(
        default=None,
        description="Pagination cursor: the unix-ms timestamp of the last row from the previous page.",
    )
    page_direction: Literal["before", "after"] = Field(
        default="before", description="Page direction relative to page_timestamp: before (default) or after."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class AclEntry(BaseModel):
    """A single grant/revoke of access for a user or user group."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    kind: Literal["user", "group"] = Field(description="Whether `id` refers to a user or a user group (Team).")
    id: str = Field(min_length=1, description="Id of the user or user group to grant/revoke.")
    permission_level: Literal[1, 3, 4, 5] | None = Field(
        description="Access level: 1=read, 3=comment, 4=edit, 5=create, or null to REMOVE access.",
    )


class UpdatePrivacyAndAccessInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    object_type: str = Field(
        min_length=1,
        description=(
            "Type of object to share, e.g. task, list, folder, space, doc, dashboard, view, goal, "
            "whiteboard, form, page, customField (ClickUp supports ~66 object types)."
        ),
    )
    object_id: str = Field(min_length=1, description="Id of the object to update sharing on.")
    workspace_id: str | None = Field(
        default=None, description="Workspace id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    private: bool | None = Field(
        default=None,
        description="Set the object private (true) or public within the Workspace (false).",
    )
    entries: list[AclEntry] | None = Field(
        default=None,
        description=(
            "User/group access changes. Each entry: {kind: user|group, id, permission_level}. "
            "permission_level null removes that user/group's access."
        ),
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> UpdatePrivacyAndAccessInput:
        if self.private is None and not self.entries:
            raise ValueError("Provide `private` and/or at least one `entries` item to update.")
        return self


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(
    name="clickup_get_workspace_plan",
    annotations=ToolAnnotations(
        title="Get ClickUp Workspace Plan",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_workspace_plan(params: GetWorkspacePlanInput) -> str:
    """Report the current subscription plan of a Workspace.

    When to Use:
    - To check whether the Workspace is on Enterprise before calling
      `clickup_query_audit_logs` / `clickup_update_privacy_and_access` or other
      Enterprise-only endpoints.

    When NOT to Use:
    - To see seat usage — use `clickup_get_workspace_seats`.

    Returns:
    The plan name and numeric plan id.

    Examples:
    - params = {}
    - params = {"team_id": "9008", "response_format": "json"}

    Error Handling:
    404 → unknown team id; 401 → bad token. Errors return an `Error ...` string.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/plan")
        data = resp.json()
        data = data if isinstance(data, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return to_json(data)

        return f"Workspace `{team_id}` plan: **{data.get('plan_name', 'unknown')}** (plan_id `{data.get('plan_id', '?')}`)."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_workspace_seats",
    annotations=ToolAnnotations(
        title="Get ClickUp Workspace Seats",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_workspace_seats(params: GetWorkspaceSeatsInput) -> str:
    """Report used, total, and available member and guest seats for a Workspace.

    When to Use:
    - Before inviting members/guests, to confirm free seats exist (an invite on a
      full paid plan may add a billable seat).

    When NOT to Use:
    - To read the plan tier — use `clickup_get_workspace_plan`.

    Returns:
    Member seats (filled/total/empty) and guest seats (filled/total/empty). Guest
    totals may be reported as "Infinity" on unlimited plans.

    Examples:
    - params = {}
    - params = {"team_id": "9008", "response_format": "json"}

    Error Handling:
    404 → unknown team id; 401 → bad token. Errors return an `Error ...` string.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/seats")
        data = resp.json()
        data = data if isinstance(data, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return to_json(data)

        members_raw = data.get("members")
        members = members_raw if isinstance(members_raw, dict) else {}
        guests_raw = data.get("guests")
        guests = guests_raw if isinstance(guests_raw, dict) else {}
        return "\n".join(
            [
                f"# Seats — Workspace `{team_id}`",
                "",
                "**Members**",
                f"- filled: {members.get('filled_members_seats', '?')}",
                f"- total: {members.get('total_member_seats', '?')}",
                f"- empty: {members.get('empty_members_seats', members.get('empty_member_seats', '?'))}",
                "",
                "**Guests**",
                f"- filled: {guests.get('filled_guest_seats', '?')}",
                f"- total: {guests.get('total_guest_seats', '?')}",
                f"- empty: {guests.get('empty_guest_seats', '?')}",
            ]
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_query_audit_logs",
    annotations=ToolAnnotations(
        title="Query ClickUp Audit Logs",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_query_audit_logs(params: QueryAuditLogsInput) -> str:
    """Query a Workspace's audit trail (Enterprise, Workspace owner only).

    Retrieves audit-log rows for a class of events (`applicability`), optionally
    narrowed by user, event type, outcome, and time window. Results are
    timestamp-paginated: pass the last row's timestamp back as `page_timestamp`
    (with `page_direction`) to walk the log.

    Note: Enterprise plan only — returns 403 on other plans. Additionally, only
    the Workspace owner's token can read audit logs; other tokens get 403.

    When to Use:
    - For a security/compliance review of who did what in the Workspace.

    When NOT to Use:
    - For task activity feeds — audit logs cover account/security/hierarchy
      administration, not per-task comment history.

    Returns:
    A list of audit entries (event, outcome, actor, time). Use response_format
    json for the raw rows including every field.

    Pagination:
    Timestamp-based. Read the timestamp of the last row, then call again with
    `page_timestamp=<that>` and `page_direction="before"` (older) or `"after"`.

    Examples:
    - params = {"applicability": "auth-and-security", "event_status": "failed"}
    - params = {"applicability": "user-activity", "user_emails": ["a@x.io"], "page_rows": 50}

    Error Handling:
    403 → not Enterprise or the token is not the Workspace owner; 400 → bad
    filter. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_team_id(params.workspace_id)

        filter_body: dict[str, Any] = {"workspaceId": workspace_id}
        if params.user_ids:
            filter_body["userId"] = params.user_ids
        if params.user_emails:
            filter_body["userEmail"] = params.user_emails
        if params.event_types:
            filter_body["eventType"] = params.event_types
        if params.event_status is not None:
            filter_body["eventStatus"] = params.event_status.value
        if params.start_time is not None:
            filter_body["startTime"] = params.start_time
        if params.end_time is not None:
            filter_body["endTime"] = params.end_time

        pagination: dict[str, Any] = {"pageRows": params.page_rows, "pageDirection": params.page_direction}
        if params.page_timestamp is not None:
            pagination["pageTimestamp"] = params.page_timestamp

        body: dict[str, Any] = {
            "applicability": params.applicability.value,
            "filter": filter_body,
            "pagination": pagination,
        }

        client = get_client()
        resp = await client.request("POST", f"/workspaces/{workspace_id}/auditlogs", json_body=body, use_v3=True)
        entries = _extract_audit_entries(resp.json())

        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json({"count": len(entries), "entries": entries}))

        lines = [f"# Audit logs — {params.applicability.value} ({len(entries)} rows)", ""]
        for entry in entries:
            lines.append(_format_audit_entry(entry))
        if not entries:
            lines.append("_No audit entries matched._")
        else:
            last_ts = entries[-1].get("timestamp") or entries[-1].get("date")
            if last_ts is not None:
                lines.append("")
                lines.append(f"_Next page: call again with page_timestamp = `{last_ts}`._")
        return _cap("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_privacy_and_access",
    annotations=ToolAnnotations(
        title="Update ClickUp Privacy and Access",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_privacy_and_access(params: UpdatePrivacyAndAccessInput) -> str:
    """Set the privacy of an object and grant/revoke user or group access (Enterprise).

    Flips an object private/public and/or edits its ACL. Each `entries` item sets
    a permission level for a user or group: 1=read, 3=comment, 4=edit, 5=create,
    or null to remove that principal's access. Works across ClickUp object types
    (task, list, folder, space, doc, dashboard, view, goal, …) via the
    `object_type`/`object_id` pair.

    Note: Enterprise plan only — returns 403 on other plans. Sharing an item may
    incur seat/billing charges.

    When to Use:
    - To make a List private and grant a group edit access.
    - To revoke a user's access to a Doc (`permission_level: null`).

    When NOT to Use:
    - To invite someone to the whole Workspace — use the guests/users tools.

    Returns:
    A confirmation of the privacy change and each access entry applied.

    Examples:
    - params = {"object_type": "list", "object_id": "901300", "private": true,
                "entries": [{"kind": "group", "id": "88", "permission_level": 4}]}
    - params = {"object_type": "doc", "object_id": "8cb", "entries":
                [{"kind": "user", "id": "182", "permission_level": null}]}

    Error Handling:
    403 → not Enterprise or insufficient rights; 404 → unknown object. Errors
    return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_team_id(params.workspace_id)

        body: dict[str, Any] = {}
        if params.private is not None:
            body["private"] = params.private
        if params.entries:
            body["entries"] = [
                {"kind": e.kind, "id": e.id, "permission_level": e.permission_level} for e in params.entries
            ]

        client = get_client()
        await client.request(
            "PATCH",
            f"/workspaces/{workspace_id}/{params.object_type}/{params.object_id}/acls",
            json_body=body,
            use_v3=True,
        )

        changes: list[str] = []
        if params.private is not None:
            changes.append("private" if params.private else "public")
        if params.entries:
            changes.append(
                "access "
                + "; ".join(f"{e.kind} `{e.id}`→{_PERMISSION_LABELS[e.permission_level]}" for e in params.entries)
            )
        return f"Updated {params.object_type} `{params.object_id}`: {', '.join(changes)}."
    except Exception as exc:
        return handle_api_error(exc)
