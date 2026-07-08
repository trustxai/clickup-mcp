"""ClickUp Webhooks tools (API v2).

Webhooks let ClickUp push real-time events (task/list/folder/space/goal changes)
to an HTTPS endpoint you control. They are Workspace-scoped and can optionally be
narrowed to a single location (a Space, Folder, List, or Task).

ClickUp reports delivery health on each webhook: the response carries a
``health`` object with a ``status`` (``active`` while deliveries succeed,
``failing`` once ClickUp starts retrying) and a ``fail_count``. After enough
consecutive failures ClickUp suspends the webhook — re-enable it with
``clickup_update_webhook`` (``status="active"``). These tools surface
``health.status`` on ``clickup_get_webhooks`` so an agent can spot a broken hook.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, to_json
from clickup_mcp.server import mcp

# Output byte guard, independent of API paging, to stay under the 1 MB MCP limit.
MAX_OUTPUT_CHARS = 800_000

# The full set of ClickUp webhook event names, grouped by resource. Pass "*" to
# subscribe to every event. Kept as a documented constant (events is accepted as
# a free-form list so newly-added ClickUp events are never rejected).
KNOWN_WEBHOOK_EVENTS: tuple[str, ...] = (
    # Task
    "taskCreated",
    "taskUpdated",
    "taskDeleted",
    "taskPriorityUpdated",
    "taskStatusUpdated",
    "taskAssigneeUpdated",
    "taskDueDateUpdated",
    "taskTagUpdated",
    "taskMoved",
    "taskCommentPosted",
    "taskCommentUpdated",
    "taskTimeEstimateUpdated",
    "taskTimeTrackedUpdated",
    # List
    "listCreated",
    "listUpdated",
    "listDeleted",
    # Folder
    "folderCreated",
    "folderUpdated",
    "folderDeleted",
    # Space
    "spaceCreated",
    "spaceUpdated",
    "spaceDeleted",
    # Goal
    "goalCreated",
    "goalUpdated",
    "goalDeleted",
    # Key result
    "keyResultCreated",
    "keyResultUpdated",
    "keyResultDeleted",
)


def _resolve_team_id(team_id: str | None) -> str:
    """Return the caller's Workspace (team) id, else the configured default."""
    resolved = (team_id or "").strip() or get_settings().clickup_team_id.strip()
    if not resolved:
        raise ValueError(
            "No Workspace id available. Pass team_id, or set CLICKUP_TEAM_ID "
            "(your Workspace/team id) in the environment or .env."
        )
    return resolved


def _cap(text: str) -> str:
    """Trim oversized output so a single response cannot blow the MCP size limit."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n\n…output truncated (response exceeded display cap)."


def _extract_webhooks(payload: Any) -> list[dict[str, Any]]:
    """Normalize the list response (keyed object vs bare array)."""
    if isinstance(payload, list):
        return [wh for wh in payload if isinstance(wh, dict)]
    if isinstance(payload, dict):
        webhooks = payload.get("webhooks") or payload.get("items") or []
        return [wh for wh in webhooks if isinstance(wh, dict)]
    return []


def _health_status(webhook: dict[str, Any]) -> tuple[str, Any]:
    """Pull (status, fail_count) out of a webhook's ``health`` object defensively."""
    health = webhook.get("health")
    if isinstance(health, dict):
        return str(health.get("status") or "unknown"), health.get("fail_count")
    return "unknown", None


def _format_webhook(webhook: dict[str, Any]) -> str:
    wid = webhook.get("id", "?")
    endpoint = webhook.get("endpoint") or "(no endpoint)"
    events = webhook.get("events") or []
    status, fail_count = _health_status(webhook)
    lines = [f"- **{endpoint}** (id `{wid}`)"]
    lines.append(f"  - health: `{status}`" + (f" (fail_count {fail_count})" if fail_count is not None else ""))
    if isinstance(events, list):
        lines.append(f"  - events: {', '.join(str(e) for e in events) if events else '(none)'}")
    for scope in ("space_id", "folder_id", "list_id", "task_id"):
        if webhook.get(scope):
            lines.append(f"  - {scope}: `{webhook.get(scope)}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class CreateWebhookInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    endpoint: str = Field(
        min_length=1,
        description="HTTPS URL ClickUp will POST events to. Must be publicly reachable and respond 200.",
    )
    events: list[str] = Field(
        min_length=1,
        description=(
            "Event names to subscribe to, e.g. ['taskCreated', 'taskStatusUpdated']. "
            "Pass ['*'] to subscribe to ALL events. Known events: taskCreated, taskUpdated, "
            "taskDeleted, taskPriorityUpdated, taskStatusUpdated, taskAssigneeUpdated, "
            "taskDueDateUpdated, taskTagUpdated, taskMoved, taskCommentPosted, taskCommentUpdated, "
            "taskTimeEstimateUpdated, taskTimeTrackedUpdated, listCreated, listUpdated, listDeleted, "
            "folderCreated, folderUpdated, folderDeleted, spaceCreated, spaceUpdated, spaceDeleted, "
            "goalCreated, goalUpdated, goalDeleted, keyResultCreated, keyResultUpdated, keyResultDeleted."
        ),
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace (team) id that owns the webhook. Defaults to CLICKUP_TEAM_ID when omitted.",
    )
    space_id: str | None = Field(
        default=None, description="Restrict the webhook to events within this Space only (optional filter)."
    )
    folder_id: str | None = Field(
        default=None, description="Restrict the webhook to events within this Folder only (optional filter)."
    )
    list_id: str | None = Field(
        default=None, description="Restrict the webhook to events within this List only (optional filter)."
    )
    task_id: str | None = Field(
        default=None, description="Restrict the webhook to events on this Task only (optional filter)."
    )


class GetWebhooksInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_id: str | None = Field(
        default=None,
        description="Workspace (team) id whose webhooks to list. Defaults to CLICKUP_TEAM_ID when omitted.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class UpdateWebhookInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    webhook_id: str = Field(min_length=1, description="Id (UUID) of the webhook to update.")
    endpoint: str | None = Field(default=None, description="New HTTPS URL for event delivery.")
    events: list[str] | None = Field(
        default=None,
        description="Replacement event list (see clickup_create_webhook for names). Pass ['*'] for all events.",
    )
    status: str | None = Field(
        default=None,
        description=(
            "Webhook delivery status. Pass 'active' to re-enable a webhook ClickUp suspended after "
            "repeated delivery failures."
        ),
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> UpdateWebhookInput:
        if self.endpoint is None and self.events is None and self.status is None:
            raise ValueError("Provide at least one of endpoint, events, or status to update.")
        return self


class DeleteWebhookInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    webhook_id: str = Field(min_length=1, description="Id (UUID) of the webhook to delete.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(
    name="clickup_create_webhook",
    annotations=ToolAnnotations(
        title="Create ClickUp Webhook",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_webhook(params: CreateWebhookInput) -> str:
    """Register a webhook that pushes ClickUp events to your endpoint.

    Subscribes an HTTPS endpoint to one or more event types across a Workspace,
    optionally narrowed to a single Space, Folder, List, or Task. Pass
    `events=['*']` to receive every event. ClickUp responds with the new webhook
    id and a `health` object (status/fail_count) reflecting delivery health.

    When to Use:
    - To wire ClickUp into an external system (CI, chatops, sync jobs) on task or
      hierarchy changes.
    - To watch a single List/Task by setting the matching location filter.

    When NOT to Use:
    - To inspect or repair existing webhooks — use `clickup_get_webhooks` then
      `clickup_update_webhook`.
    - For one-off reads of data — call the relevant read tool directly.

    Returns:
    A confirmation with the new webhook id, its health status, and the subscribed
    events.

    Examples:
    - params = {"endpoint": "https://x.io/hook", "events": ["taskCreated", "taskUpdated"]}
    - params = {"endpoint": "https://x.io/hook", "events": ["*"], "list_id": "901300"}

    Error Handling:
    400 → invalid/unreachable endpoint or unknown event name; 404 → unknown
    team/location id. Errors return an `Error ...` string.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        body: dict[str, Any] = {"endpoint": params.endpoint, "events": params.events}
        for scope in ("space_id", "folder_id", "list_id", "task_id"):
            value = getattr(params, scope)
            if value:
                body[scope] = value

        client = get_client()
        resp = await client.request("POST", f"/team/{team_id}/webhook", json_body=body)
        payload = resp.json()
        payload = payload if isinstance(payload, dict) else {}
        new_id = payload.get("id", "?")
        webhook_raw = payload.get("webhook")
        webhook = webhook_raw if isinstance(webhook_raw, dict) else {}
        status, _ = _health_status(webhook)
        return (
            f"Created webhook `{new_id}` → **{params.endpoint}** (health `{status}`), "
            f"subscribed to: {', '.join(params.events)}."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_webhooks",
    annotations=ToolAnnotations(
        title="Get ClickUp Webhooks",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_webhooks(params: GetWebhooksInput) -> str:
    """List the webhooks registered in a Workspace, with their delivery health.

    Returns every webhook the authenticating token created for the Workspace,
    surfacing each hook's `health.status` (`active` / `failing`) and `fail_count`
    so you can spot a broken endpoint before re-pointing or re-enabling it.

    When to Use:
    - To find a webhook's id before `clickup_update_webhook` / `clickup_delete_webhook`.
    - To audit which endpoints are subscribed and whether any are failing.

    When NOT to Use:
    - To create a new webhook — use `clickup_create_webhook`.

    Returns:
    A list of webhooks (endpoint, id, health status, events, and any location
    filter). Only webhooks created by the authenticating user are returned.

    Examples:
    - params = {}
    - params = {"team_id": "9008", "response_format": "json"}

    Error Handling:
    404 → unknown team id; 401 → bad token. Errors return an `Error ...` string.
    """
    try:
        team_id = _resolve_team_id(params.team_id)
        client = get_client()
        resp = await client.request("GET", f"/team/{team_id}/webhook")
        webhooks = _extract_webhooks(resp.json())

        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json({"count": len(webhooks), "webhooks": webhooks}))

        lines = [f"# Webhooks ({len(webhooks)})", ""]
        for webhook in webhooks:
            lines.append(_format_webhook(webhook))
        if not webhooks:
            lines.append("_No webhooks registered for this Workspace._")
        return _cap("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_update_webhook",
    annotations=ToolAnnotations(
        title="Update ClickUp Webhook",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_update_webhook(params: UpdateWebhookInput) -> str:
    """Change a webhook's endpoint, event subscription, or delivery status.

    Update any combination of the destination `endpoint`, the `events` list, and
    the `status`. Setting `status='active'` re-enables a webhook ClickUp
    suspended after repeated delivery failures. ClickUp treats these fields as a
    full replacement, so to preserve the current endpoint/events while flipping
    status, re-supply them (read the current values from `clickup_get_webhooks`).

    When to Use:
    - To re-point a webhook at a new URL, or to broaden/narrow its events.
    - To reactivate a hook that stopped firing (`status='active'`).

    When NOT to Use:
    - To register a brand-new webhook — use `clickup_create_webhook`.
    - To remove a webhook entirely — use `clickup_delete_webhook`.

    Returns:
    A confirmation naming the webhook id and the fields that changed.

    Examples:
    - params = {"webhook_id": "e50...", "status": "active"}
    - params = {"webhook_id": "e50...", "endpoint": "https://x.io/hook2", "events": ["*"]}

    Error Handling:
    400 → invalid endpoint/event; 404 → unknown webhook id. Errors return an
    `Error ...` string.
    """
    try:
        body: dict[str, Any] = {}
        if params.endpoint is not None:
            body["endpoint"] = params.endpoint
        if params.events is not None:
            body["events"] = params.events
        if params.status is not None:
            body["status"] = params.status

        client = get_client()
        await client.request("PUT", f"/webhook/{params.webhook_id}", json_body=body)
        changed = ", ".join(sorted(body.keys()))
        return f"Updated webhook `{params.webhook_id}` ({changed})."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_webhook",
    annotations=ToolAnnotations(
        title="Delete ClickUp Webhook",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_webhook(params: DeleteWebhookInput) -> str:
    """Permanently delete a webhook, stopping all event delivery to its endpoint.

    When to Use:
    - To decommission an endpoint or clean up a failing/duplicate webhook.

    When NOT to Use:
    - To temporarily pause delivery — there is no pause; recreate with
      `clickup_create_webhook` when needed, or leave it and ignore the events.

    Returns:
    A confirmation that the webhook was deleted.

    Examples:
    - params = {"webhook_id": "e506-4a29-9d42-26e504e3435e"}

    Error Handling:
    404 → unknown webhook id. Errors return an `Error ...` string.
    """
    try:
        client = get_client()
        await client.request("DELETE", f"/webhook/{params.webhook_id}")
        return f"Deleted webhook `{params.webhook_id}`."
    except Exception as exc:
        return handle_api_error(exc)
