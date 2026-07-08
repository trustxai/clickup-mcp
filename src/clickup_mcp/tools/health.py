"""Health-check tool — the in-repo exemplar of the house tool style.

Wave workers: copy this module's shape (decorator, annotations, input model,
docstring sections, try/except -> handle_api_error, `-> str` return).
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.server import mcp


@mcp.tool(
    name="clickup_health_check",
    annotations=ToolAnnotations(
        title="ClickUp Health Check",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_health_check() -> str:
    """Verify connectivity and authentication against the ClickUp API.

    Calls `GET /user` (the authorized-user endpoint) with the configured
    CLICKUP_API_TOKEN and reports who the token belongs to.

    When to Use:
    - As the first call after configuring the server, to confirm the token works.
    - To debug 401/403 responses from other tools.

    When NOT to Use:
    - To look up other people in the Workspace (use the members tools instead).

    Returns:
    A one-line `OK` summary with the authorized user's name, id, and email, or
    an `Error ...` string describing the failure.

    Error Handling:
    401 means the token is missing/invalid; connection errors point to network
    or CLICKUP_API_URL issues.
    """
    try:
        client = get_client()
        resp = await client.request("GET", "/user")
        user = resp.json().get("user", {})
        username = user.get("username") or "unknown"
        user_id = user.get("id", "unknown")
        email = user.get("email") or "unknown"
        return f"OK — authenticated as **{username}** (id {user_id}, {email})."
    except Exception as exc:
        return handle_api_error(exc)
