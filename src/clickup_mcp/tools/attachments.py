"""Attachment tools — upload files and list entity attachments (inventory H).

Three tools spanning both API versions:

- `clickup_create_task_attachment` (v2) uploads a local file to a task via a
  ``multipart/form-data`` request whose file field is named ``attachment``.
- `clickup_get_entity_attachments` (v3) lists the attachments of an *entity* —
  either a task or a File-type Custom Field — with cursor pagination.
- `clickup_create_entity_attachment` (v3) uploads a local file to either entity
  type; uploading to a Custom Field then requires
  ``clickup_set_custom_field_value`` to attach the uploaded file to a task.

File inputs are LOCAL paths, validated for existence and against a size cap
before upload. Tool results never inline the file bytes — they return the
ClickUp attachment id and URL only.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

_CONFIG = ConfigDict(str_strip_whitespace=True, extra="forbid")

# Client-side guard so we never read an unbounded file into memory for upload.
# This is NOT ClickUp's own limit (which is plan-dependent); it is a sane ceiling.
MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024  # 100 MiB


class EntityType(StrEnum):
    """v3 attachment parent entity type (the literal path segment)."""

    ATTACHMENTS = "attachments"  # the entity is a task (entity_id = task ID)
    CUSTOM_FIELDS = "custom_fields"  # the entity is a File-type Custom Field (entity_id = field ID)


def _validate_local_file(value: str) -> str:
    """Validate that ``value`` is an existing, non-empty, not-too-large local file."""
    path = Path(value).expanduser()
    if not path.exists():
        raise ValueError(f"File not found: {value}")
    if not path.is_file():
        raise ValueError(f"Not a regular file: {value}")
    size = path.stat().st_size
    if size == 0:
        raise ValueError(f"File is empty: {value}")
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"File is too large to upload ({size:,} bytes); the client cap is {MAX_ATTACHMENT_BYTES:,} bytes."
        )
    return value


def _load_file(file_path: str, filename: str | None) -> tuple[str, bytes]:
    """Read a validated file and return ``(display_name, bytes)`` for multipart upload."""
    path = Path(file_path).expanduser()
    return (filename or path.name), path.read_bytes()


def _extract_attachments(payload: Any) -> list[dict[str, Any]]:
    """Defensively pull the attachment list from a v3 response (bare array or keyed object)."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("attachments", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_next_cursor(payload: Any) -> str | None:
    """v3 cursor pagination is asymmetric: request ``cursor``, response ``next_cursor``."""
    if isinstance(payload, dict):
        for key in ("next_cursor", "cursor", "last_page"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _format_attachment(item: dict[str, Any]) -> str:
    """Render one attachment as a markdown bullet (URL + id, never file bytes)."""
    title = item.get("title") or item.get("filename") or "(untitled)"
    att_id = item.get("id", "unknown")
    url = item.get("url") or item.get("url_w_query") or "N/A"
    size = item.get("size")
    created = item.get("date_created") or item.get("date")
    parts = [f"- **{title}** (id `{att_id}`)"]
    if size is not None:
        parts.append(f"  - size: {size:,} bytes" if isinstance(size, int) else f"  - size: {size}")
    if created is not None:
        parts.append(f"  - created: {epoch_to_human(created)}")
    parts.append(f"  - url: {url}")
    return "\n".join(parts)


class CreateTaskAttachmentInput(BaseModel):
    """Upload a local file to a task (v2 ``multipart/form-data``)."""

    model_config = _CONFIG

    task_id: str = Field(..., min_length=1, description="The task to attach the file to.")
    file_path: str = Field(
        ...,
        min_length=1,
        description="Path to a LOCAL file to upload. Must exist and be under the size cap. Cloud-stored files are not supported.",
    )
    filename: str | None = Field(
        default=None,
        description="Optional display name for the attachment; defaults to the file's basename.",
    )
    custom_task_ids: bool = Field(
        default=False,
        description="Set true to interpret task_id as a custom task ID. Requires team_id.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace (team) ID — required only when custom_task_ids is true.",
    )

    _check_file = field_validator("file_path")(_validate_local_file)

    @model_validator(mode="after")
    def _require_team_id(self) -> CreateTaskAttachmentInput:
        if self.custom_task_ids and not self.team_id:
            raise ValueError("team_id is required when custom_task_ids is true.")
        return self

    def custom_id_query(self) -> dict[str, Any]:
        if not self.custom_task_ids:
            return {}
        query: dict[str, Any] = {"custom_task_ids": "true"}
        if self.team_id:
            query["team_id"] = self.team_id
        return query


class GetEntityAttachmentsInput(BaseModel):
    """List the attachments of a v3 entity (task or File Custom Field)."""

    model_config = _CONFIG

    workspace_id: str = Field(..., min_length=1, description="Workspace (team) ID that owns the entity.")
    entity_type: EntityType = Field(
        ...,
        description="'attachments' when the entity is a task, or 'custom_fields' for a File-type Custom Field.",
    )
    entity_id: str = Field(
        ..., min_length=1, description="The task ID (for 'attachments') or Custom Field ID (for 'custom_fields')."
    )
    cursor: str | None = Field(
        default=None,
        description="Opaque cursor from a previous page's next_cursor; omit for the first page.",
    )
    limit: int = Field(default=50, ge=1, le=100, description="Max attachments to return per page (1-100, default 50).")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: markdown (default) or json.",
    )


class CreateEntityAttachmentInput(BaseModel):
    """Upload a local file to a v3 entity (task or File Custom Field)."""

    model_config = _CONFIG

    workspace_id: str = Field(..., min_length=1, description="Workspace (team) ID that owns the entity.")
    entity_type: EntityType = Field(
        ...,
        description="'attachments' to upload to a task, or 'custom_fields' to upload to a File-type Custom Field.",
    )
    entity_id: str = Field(
        ..., min_length=1, description="The task ID (for 'attachments') or Custom Field ID (for 'custom_fields')."
    )
    file_path: str = Field(
        ...,
        min_length=1,
        description="Path to a LOCAL file to upload. Must exist and be under the size cap.",
    )
    filename: str | None = Field(
        default=None,
        description="Optional display name for the attachment; defaults to the file's basename.",
    )

    _check_file = field_validator("file_path")(_validate_local_file)


@mcp.tool(
    name="clickup_create_task_attachment",
    annotations=ToolAnnotations(
        title="Create Task Attachment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_task_attachment(params: CreateTaskAttachmentInput) -> str:
    """Upload a local file to a task as an attachment (v2).

    Reads the file at `file_path` from the local filesystem and posts it to the
    task as ``multipart/form-data`` (the ClickUp file field is ``attachment``).
    Files already stored in the cloud cannot be used here.

    When to Use:
    - To attach a document, image, or log file that exists on the local disk to
      a task.

    When NOT to Use:
    - To upload to a File-type Custom Field, or if you prefer the newer API — use
      `clickup_create_entity_attachment` (v3).
    - To read existing attachments — use `clickup_get_entity_attachments`.

    Returns:
    A confirmation with the new attachment's id and URL. The file bytes are
    never echoed back.

    Examples:
    - `params = {"task_id": "abc", "file_path": "/tmp/report.pdf"}`
    - custom task ID: `params = {"task_id": "PROJ-1", "file_path": "./log.txt", "custom_task_ids": true, "team_id": "123"}`

    Error Handling:
    A ValidationError before the request means the path is missing/empty/too
    large; a 404 means the task ID is wrong.
    """
    try:
        client = get_client()
        name, data = _load_file(params.file_path, params.filename)
        resp = await client.request(
            "POST",
            f"/task/{params.task_id}/attachment",
            files={"attachment": (name, data)},
            params=params.custom_id_query() or None,
        )
        payload = resp.json()
        att_id = payload.get("id", "unknown") if isinstance(payload, dict) else "unknown"
        url = payload.get("url", "N/A") if isinstance(payload, dict) else "N/A"
        title = (payload.get("title") if isinstance(payload, dict) else None) or name
        return f"Uploaded attachment **{title}** (id `{att_id}`) to task **{params.task_id}**.\nURL: {url}"
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_entity_attachments",
    annotations=ToolAnnotations(
        title="Get Entity Attachments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_entity_attachments(params: GetEntityAttachmentsInput) -> str:
    """List the attachments of a task or File-type Custom Field (v3).

    Calls ``GET /v3/workspaces/{workspace_id}/{entity_type}/{entity_id}/attachments``
    and returns the page of attachments plus the cursor for the next page.

    When to Use:
    - To enumerate the files attached to a task (entity_type='attachments') or a
      File Custom Field (entity_type='custom_fields').

    When NOT to Use:
    - To upload a file — use `clickup_create_entity_attachment` or
      `clickup_create_task_attachment`.

    Returns:
    Each attachment's title, id, size, and URL. When more results exist, the
    response includes the cursor to pass back as `cursor` for the next page.

    Pagination:
    Cursor-based and asymmetric — the request takes `cursor`, the response
    returns `next_cursor`. Loop: pass the returned next_cursor back as `cursor`
    until it is absent.

    Examples:
    - task: `params = {"workspace_id": "123", "entity_type": "attachments", "entity_id": "abc"}`
    - custom field: `params = {"workspace_id": "123", "entity_type": "custom_fields", "entity_id": "fld-1", "limit": 20}`

    Error Handling:
    A 404 means the entity_id was not found — or the v3 attachments surface is
    not available on this Workspace at all (verified live on a Business-plan
    workspace: even reads 404). If both tools here 404 on ids that definitely
    exist, fall back to `clickup_create_task_attachment` (v2) for task files.
    A 400 usually means entity_type and entity_id do not match (e.g. a Custom
    Field ID with entity_type='attachments').
    """
    try:
        client = get_client()
        query: dict[str, Any] = {"limit": params.limit}
        if params.cursor:
            query["cursor"] = params.cursor
        resp = await client.request(
            "GET",
            f"/workspaces/{params.workspace_id}/{params.entity_type.value}/{params.entity_id}/attachments",
            params=query,
            use_v3=True,
        )
        payload = resp.json()
        items = _extract_attachments(payload)
        next_cursor = _extract_next_cursor(payload)

        if params.response_format is ResponseFormat.JSON:
            return to_json({"count": len(items), "next_cursor": next_cursor, "attachments": items})

        title = f"Attachments for {params.entity_type.value} `{params.entity_id}`"
        lines = [f"# {title}", "", f"Showing **{len(items):,}** attachment(s)."]
        if next_cursor:
            lines.append(f"More available — pass `cursor={next_cursor}` for the next page.")
        lines.append("")
        if items:
            lines.extend(_format_attachment(item) for item in items)
        else:
            lines.append("_No attachments._")
        return "\n".join(lines)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_entity_attachment",
    annotations=ToolAnnotations(
        title="Create Entity Attachment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_entity_attachment(params: CreateEntityAttachmentInput) -> str:
    """Upload a local file to a task or File-type Custom Field (v3).

    Reads the file at `file_path` and posts it as ``multipart/form-data`` to
    ``POST /v3/workspaces/{workspace_id}/{entity_type}/{entity_id}/attachments``.
    After uploading to a Custom Field (entity_type='custom_fields'), call
    `clickup_set_custom_field_value` to associate the uploaded file with a task.

    When to Use:
    - The modern replacement for `clickup_create_task_attachment`, and the only
      way to upload to a File-type Custom Field.

    When NOT to Use:
    - To read existing attachments — use `clickup_get_entity_attachments`.

    Returns:
    A confirmation with the new attachment's id and URL (never the file bytes).

    Examples:
    - task: `params = {"workspace_id": "123", "entity_type": "attachments", "entity_id": "abc", "file_path": "/tmp/a.png"}`
    - custom field: `params = {"workspace_id": "123", "entity_type": "custom_fields", "entity_id": "fld-1", "file_path": "./a.png"}`

    Error Handling:
    A ValidationError before the request means the path is missing/empty/too
    large; a 404 means the entity_id was not found — or the v3 attachments
    surface is not available on this Workspace (verified live: some plans 404
    on this endpoint entirely; use `clickup_create_task_attachment` for task
    files in that case).
    """
    try:
        client = get_client()
        name, data = _load_file(params.file_path, params.filename)
        form: dict[str, Any] | None = {"filename": params.filename} if params.filename else None
        resp = await client.request(
            "POST",
            f"/workspaces/{params.workspace_id}/{params.entity_type.value}/{params.entity_id}/attachments",
            files={"attachment": (name, data)},
            data=form,
            use_v3=True,
        )
        payload = resp.json()
        att_id = payload.get("id", "unknown") if isinstance(payload, dict) else "unknown"
        url = payload.get("url", "N/A") if isinstance(payload, dict) else "N/A"
        title = (payload.get("title") if isinstance(payload, dict) else None) or name
        return (
            f"Uploaded attachment **{title}** (id `{att_id}`) to {params.entity_type.value} "
            f"**{params.entity_id}**.\nURL: {url}"
        )
    except Exception as exc:
        return handle_api_error(exc)
