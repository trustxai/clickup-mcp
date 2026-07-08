"""ClickUp Docs tools (API v3).

Docs, their page tree, and page content all live on the ClickUp v3 surface
(`/api/v3/workspaces/{workspace_id}/docs...`), so every request here goes through
`client.request(..., use_v3=True)`.

The headline capability is `clickup_create_doc`: it can place a brand-new Doc
directly inside its final home — a Space, Folder, List, the Everything level, or
the Workspace root — via the `parent` object, so callers never have to create a
Doc and drag it afterwards.

Wave workers (chat modules especially): search uses **cursor** pagination — the
response carries `next_cursor`, which you pass back as the `cursor` query param on
the next call. The page-listing path is `page_listing` (snake_case), not
`pageListing`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.config import get_settings
from clickup_mcp.errors import handle_api_error
from clickup_mcp.formatters import ResponseFormat, epoch_to_human, to_json
from clickup_mcp.server import mcp

# Display / byte guards independent of API paging, to stay under the 1 MB MCP limit.
MAX_DISPLAY_ITEMS = 50
MAX_OUTPUT_CHARS = 800_000


class ParentType(StrEnum):
    """Where a Doc lives in the hierarchy (friendly name for the numeric enum)."""

    SPACE = "space"
    FOLDER = "folder"
    LIST = "list"
    EVERYTHING = "everything"
    WORKSPACE = "workspace"


# createdoc `parent.type` is a numeric enum; search's `parent_type` filter is the
# upper-case name. Keep both mappings next to the friendly enum above.
_PARENT_TYPE_TO_INT: dict[ParentType, int] = {
    ParentType.SPACE: 4,
    ParentType.FOLDER: 5,
    ParentType.LIST: 6,
    ParentType.EVERYTHING: 7,
    ParentType.WORKSPACE: 12,
}


class ContentFormat(StrEnum):
    """Format of page content on the wire."""

    MARKDOWN = "text/md"
    PLAIN = "text/plain"


class ContentEditMode(StrEnum):
    """How `content` is merged into an existing page on edit."""

    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"


class Visibility(StrEnum):
    """Doc visibility at creation time."""

    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"


def _resolve_workspace_id(workspace_id: str | None) -> str:
    """Return the caller's workspace id, else the configured default team id."""
    resolved = (workspace_id or "").strip() or get_settings().clickup_team_id.strip()
    if not resolved:
        raise ValueError(
            "No Workspace id available. Pass workspace_id, or set CLICKUP_TEAM_ID "
            "(your Workspace/team id) in the environment or .env."
        )
    return resolved


def _cap(text: str) -> str:
    """Trim oversized output so a single response cannot blow the MCP size limit."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n\n…output truncated (response exceeded display cap)."


# ---------------------------------------------------------------------------
# Response-shape helpers (ClickUp v3 is loose about bare-array vs keyed-object)
# ---------------------------------------------------------------------------
def _extract_docs(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        docs = payload.get("docs") or payload.get("items") or []
        cursor = payload.get("next_cursor") or payload.get("cursor")
        return list(docs), cursor
    return [], None


def _extract_pages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return list(payload.get("pages") or payload.get("items") or [])
    return []


def _extract_page_listing(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return list(payload.get("page_listing") or payload.get("pages") or payload.get("items") or [])
    return []


def _format_doc(doc: dict[str, Any]) -> str:
    name = doc.get("name") or "(untitled)"
    doc_id = doc.get("id", "?")
    parts = [f"- **{name}** (id `{doc_id}`)"]
    parent = doc.get("parent")
    if isinstance(parent, dict) and parent.get("id") is not None:
        parts.append(f" — parent type `{parent.get('type')}` id `{parent.get('id')}`")
    created = doc.get("date_created")
    if created:
        parts.append(f", created {epoch_to_human(created)}")
    return "".join(parts)


def _format_doc_detail(doc: dict[str, Any]) -> str:
    lines = [
        f"# {doc.get('name') or '(untitled)'}",
        "",
        f"- id: `{doc.get('id', '?')}`",
    ]
    parent = doc.get("parent")
    if isinstance(parent, dict):
        lines.append(f"- parent: type `{parent.get('type')}` id `{parent.get('id')}`")
    if doc.get("workspace_id") is not None:
        lines.append(f"- workspace_id: `{doc.get('workspace_id')}`")
    if doc.get("creator") is not None:
        lines.append(f"- creator: `{doc.get('creator')}`")
    lines.append(f"- created: {epoch_to_human(doc.get('date_created'))}")
    lines.append(f"- updated: {epoch_to_human(doc.get('date_updated'))}")
    return "\n".join(lines)


def _format_page(page: dict[str, Any]) -> str:
    lines = [f"## {page.get('name') or '(untitled page)'}", ""]
    lines.append(f"- id: `{page.get('id', '?')}`")
    if page.get("sub_title"):
        lines.append(f"- sub_title: {page.get('sub_title')}")
    if page.get("parent_page_id"):
        lines.append(f"- parent_page_id: `{page.get('parent_page_id')}`")
    content = page.get("content")
    if content:
        lines.extend(["", "---", "", str(content)])
    return "\n".join(lines)


def _format_listing_items(items: list[dict[str, Any]], depth: int = 0) -> list[str]:
    lines: list[str] = []
    indent = "  " * depth
    for item in items:
        name = item.get("name") or "(untitled page)"
        lines.append(f"{indent}- **{name}** (id `{item.get('id', '?')}`)")
        children = item.get("pages")
        if isinstance(children, list) and children:
            lines.extend(_format_listing_items(children, depth + 1))
    return lines


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class SearchDocsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    workspace_id: str | None = Field(
        default=None,
        description="Workspace (team) id to search within. Defaults to CLICKUP_TEAM_ID when omitted.",
    )
    doc_id: str | None = Field(default=None, description="Return only the Doc with this exact id.")
    creator: int | None = Field(default=None, description="Filter to Docs created by this user id.")
    deleted: bool = Field(default=False, description="Include deleted Docs in the results.")
    archived: bool = Field(default=False, description="Include archived Docs in the results.")
    parent_id: str | None = Field(default=None, description="Filter to Docs whose parent has this id.")
    parent_type: ParentType | None = Field(
        default=None,
        description="Filter by parent location type (space, folder, list, everything, workspace).",
    )
    limit: int = Field(default=50, ge=10, le=100, description="Docs per page (10–100). ClickUp default is 50.")
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor. Pass the `next_cursor` value from a previous response to get the next page.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class GetDocInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_id: str = Field(min_length=1, description="Id of the Doc to fetch.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class CreateDocInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, description="Title of the new Doc.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id to create the Doc in. Defaults to CLICKUP_TEAM_ID."
    )
    parent_id: str | None = Field(
        default=None,
        description="Id of the parent container (Space/Folder/List id, or Workspace id). Required with parent_type.",
    )
    parent_type: ParentType | None = Field(
        default=None,
        description="Type of the parent container. Required with parent_id. Omit both to use the default location.",
    )
    visibility: Visibility | None = Field(default=None, description="Doc visibility: PRIVATE or PUBLIC.")
    create_page: bool = Field(
        default=True, description="Create an initial empty page with the Doc (ClickUp default is true)."
    )

    @model_validator(mode="after")
    def _parent_pair(self) -> CreateDocInput:
        if (self.parent_id is None) != (self.parent_type is None):
            raise ValueError("parent_id and parent_type must be provided together (or both omitted).")
        return self


class DocPageListingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_id: str = Field(min_length=1, description="Id of the Doc whose page tree to list.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    max_page_depth: int = Field(
        default=-1, ge=-1, description="How many levels of subpages to include; -1 (default) means no limit."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class DocPagesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_id: str = Field(min_length=1, description="Id of the Doc whose pages (with content) to fetch.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format for page content: text/md (default) or text/plain."
    )
    max_page_depth: int = Field(
        default=-1, ge=-1, description="How many levels of subpages to include; -1 (default) means no limit."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class CreatePageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_id: str = Field(min_length=1, description="Id of the Doc to add the page to.")
    name: str = Field(min_length=1, description="Title of the new page.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    parent_page_id: str | None = Field(
        default=None, description="Id of the parent page to nest this page under (omit for a top-level page)."
    )
    sub_title: str | None = Field(default=None, description="Optional subtitle shown under the page title.")
    content: str | None = Field(default=None, description="Page body. Markdown unless content_format is text/plain.")
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format of `content`: text/md (default) or text/plain."
    )


class GetPageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_id: str = Field(min_length=1, description="Id of the Doc that owns the page.")
    page_id: str = Field(min_length=1, description="Id of the page to fetch.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format for page content: text/md (default) or text/plain."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: markdown (default) or json."
    )


class EditPageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    doc_id: str = Field(min_length=1, description="Id of the Doc that owns the page.")
    page_id: str = Field(min_length=1, description="Id of the page to edit.")
    workspace_id: str | None = Field(
        default=None, description="Workspace (team) id. Defaults to CLICKUP_TEAM_ID when omitted."
    )
    name: str | None = Field(default=None, description="New page title (omit to leave unchanged).")
    sub_title: str | None = Field(default=None, description="New subtitle (omit to leave unchanged).")
    content: str | None = Field(default=None, description="New/added page content (omit to leave unchanged).")
    content_edit_mode: ContentEditMode = Field(
        default=ContentEditMode.REPLACE,
        description="How `content` is applied: replace (default), append, or prepend.",
    )
    content_format: ContentFormat = Field(
        default=ContentFormat.MARKDOWN, description="Format of `content`: text/md (default) or text/plain."
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> EditPageInput:
        if self.name is None and self.sub_title is None and self.content is None:
            raise ValueError("Provide at least one of name, sub_title, or content to edit.")
        return self


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(
    name="clickup_search_docs",
    annotations=ToolAnnotations(
        title="Search ClickUp Docs",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_search_docs(params: SearchDocsInput) -> str:
    """Search the Docs in a Workspace, optionally filtered by parent location.

    Lists Docs across the Workspace and can narrow by creator, parent container,
    or archived/deleted state. Results are cursor-paginated.

    When to Use:
    - To find a Doc's id before reading (`clickup_get_doc`) or editing its pages.
    - To enumerate every Doc under a Space/Folder/List (set parent_id + parent_type).

    When NOT to Use:
    - To read a specific Doc's content — use `clickup_get_doc_pages` instead.
    - To create a Doc — use `clickup_create_doc`.

    Returns:
    A list of Docs (name, id, parent, created date). When more results exist the
    response includes a `next_cursor`; pass it back as `cursor` to page forward.

    Pagination:
    Cursor-based. Loop: call once, read `next_cursor` from the output, then call
    again with `cursor=<next_cursor>` until it is empty.

    Examples:
    - params = {"parent_id": "901300", "parent_type": "space"}
    - params = {"cursor": "eyJ...", "limit": 100}

    Error Handling:
    401 → bad token; 404 → unknown Workspace id. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {
            "deleted": params.deleted,
            "archived": params.archived,
            "limit": params.limit,
        }
        if params.doc_id:
            query["id"] = params.doc_id
        if params.creator is not None:
            query["creator"] = params.creator
        if params.parent_id:
            query["parent_id"] = params.parent_id
        if params.parent_type is not None:
            query["parent_type"] = params.parent_type.upper()
        if params.cursor:
            query["cursor"] = params.cursor

        client = get_client()
        resp = await client.request("GET", f"/workspaces/{workspace_id}/docs", params=query, use_v3=True)
        docs, next_cursor = _extract_docs(resp.json())

        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json({"count": len(docs), "next_cursor": next_cursor, "docs": docs}))

        shown = docs[:MAX_DISPLAY_ITEMS]
        lines = [f"# Docs ({len(docs)} found)", ""]
        if len(docs) > len(shown):
            lines.append(f"_Showing first {len(shown)}._")
        for doc in shown:
            lines.append(_format_doc(doc))
        if not docs:
            lines.append("_No Docs matched._")
        if next_cursor:
            lines.append("")
            lines.append(f"More available — call again with `cursor` = `{next_cursor}`.")
        return _cap("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_doc",
    annotations=ToolAnnotations(
        title="Create ClickUp Doc",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_doc(params: CreateDocInput) -> str:
    """Create a Doc, optionally placed directly in its final location.

    Set `parent_id` + `parent_type` to create the Doc inside a Space, Folder,
    List, the Everything level, or the Workspace root in one call — no
    create-then-drag. The parent type maps to ClickUp's numeric enum:
    space→4, folder→5, list→6, everything→7, workspace→12. Omit both parent
    fields to create the Doc in the default (private) location.

    When to Use:
    - To start a new Doc where it belongs (e.g. a spec Doc inside a project List).

    When NOT to Use:
    - To add a page to an existing Doc — use `clickup_create_page`.
    - To change an existing page's text — use `clickup_edit_page`.

    Returns:
    A confirmation with the new Doc's name, id, and parent location.

    Examples:
    - params = {"name": "Q3 Spec", "parent_id": "901300", "parent_type": "list"}
    - params = {"name": "Scratch", "create_page": false}

    Error Handling:
    400 → bad parent id/type pairing; 404 → parent not found. Errors return an
    `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {"name": params.name, "create_page": params.create_page}
        if params.visibility is not None:
            body["visibility"] = params.visibility.value
        if params.parent_id is not None and params.parent_type is not None:
            body["parent"] = {"id": params.parent_id, "type": _PARENT_TYPE_TO_INT[params.parent_type]}

        client = get_client()
        resp = await client.request("POST", f"/workspaces/{workspace_id}/docs", json_body=body, use_v3=True)
        payload = resp.json()
        doc = payload.get("doc") if isinstance(payload, dict) and "doc" in payload else payload
        doc = doc if isinstance(doc, dict) else {}
        new_id = doc.get("id", "?")
        where = (
            f" in {params.parent_type.value} `{params.parent_id}`"
            if params.parent_type is not None
            else " in the default location"
        )
        return f"Created Doc **{params.name}** (id `{new_id}`){where}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_doc",
    annotations=ToolAnnotations(
        title="Get ClickUp Doc",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_doc(params: GetDocInput) -> str:
    """Fetch a single Doc's metadata (name, parent, timestamps).

    When to Use:
    - To confirm a Doc's parent location or creation details by id.

    When NOT to Use:
    - To read the Doc's text — use `clickup_get_doc_pages`.
    - To list its page tree — use `clickup_get_doc_page_listing`.

    Returns:
    The Doc's name, id, parent, workspace, creator, and created/updated times.

    Examples:
    - params = {"doc_id": "8cbq..."}

    Error Handling:
    404 → Doc not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        client = get_client()
        resp = await client.request("GET", f"/workspaces/{workspace_id}/docs/{params.doc_id}", use_v3=True)
        payload = resp.json()
        doc = payload.get("doc") if isinstance(payload, dict) and "doc" in payload else payload
        doc = doc if isinstance(doc, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return to_json(doc)
        return _format_doc_detail(doc)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_doc_page_listing",
    annotations=ToolAnnotations(
        title="Get ClickUp Doc Page Listing",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_doc_page_listing(params: DocPageListingInput) -> str:
    """List a Doc's page tree (titles and ids only, nested — no content).

    A lightweight outline of the Doc: the hierarchy of pages and subpages with
    their ids, without fetching any body text.

    When to Use:
    - To see a Doc's structure and grab a specific page id cheaply.

    When NOT to Use:
    - To read page content — use `clickup_get_doc_pages` or `clickup_get_page`.

    Returns:
    An indented tree of page names and ids.

    Examples:
    - params = {"doc_id": "8cbq...", "max_page_depth": 2}

    Error Handling:
    404 → Doc not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"max_page_depth": params.max_page_depth}
        client = get_client()
        resp = await client.request(
            "GET", f"/workspaces/{workspace_id}/docs/{params.doc_id}/page_listing", params=query, use_v3=True
        )
        items = _extract_page_listing(resp.json())
        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json({"count": len(items), "page_listing": items}))
        lines = [f"# Page listing for Doc `{params.doc_id}`", ""]
        listing_lines = _format_listing_items(items)
        lines.extend(listing_lines or ["_No pages._"])
        return _cap("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_doc_pages",
    annotations=ToolAnnotations(
        title="Get ClickUp Doc Pages",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_doc_pages(params: DocPagesInput) -> str:
    """Fetch a Doc's pages *with content* (the full readable body of the Doc).

    When to Use:
    - To read everything a Doc contains in one call.

    When NOT to Use:
    - To read just one page — use `clickup_get_page`.
    - To see structure without content — use `clickup_get_doc_page_listing`.

    Returns:
    Each page's title, subtitle, and content (markdown by default). Very large
    Docs are truncated with a note.

    Examples:
    - params = {"doc_id": "8cbq...", "content_format": "text/plain"}

    Error Handling:
    404 → Doc not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {
            "content_format": params.content_format.value,
            "max_page_depth": params.max_page_depth,
        }
        client = get_client()
        resp = await client.request(
            "GET", f"/workspaces/{workspace_id}/docs/{params.doc_id}/pages", params=query, use_v3=True
        )
        pages = _extract_pages(resp.json())
        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json({"count": len(pages), "pages": pages}))
        lines = [f"# Pages for Doc `{params.doc_id}` ({len(pages)})", ""]
        for page in pages:
            lines.append(_format_page(page))
            lines.append("")
        if not pages:
            lines.append("_No pages._")
        return _cap("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_create_page",
    annotations=ToolAnnotations(
        title="Create ClickUp Doc Page",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_create_page(params: CreatePageInput) -> str:
    """Add a page to an existing Doc (optionally nested under another page).

    When to Use:
    - To append a new section/page to a Doc you already created or found.

    When NOT to Use:
    - To create the Doc itself — use `clickup_create_doc`.
    - To modify an existing page — use `clickup_edit_page`.

    Returns:
    A confirmation with the new page's name and id.

    Examples:
    - params = {"doc_id": "8cbq...", "name": "Overview", "content": "# Hi"}
    - params = {"doc_id": "8cbq...", "name": "Detail", "parent_page_id": "abc"}

    Error Handling:
    404 → Doc/parent page not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {"name": params.name, "content_format": params.content_format.value}
        if params.parent_page_id is not None:
            body["parent_page_id"] = params.parent_page_id
        if params.sub_title is not None:
            body["sub_title"] = params.sub_title
        if params.content is not None:
            body["content"] = params.content

        client = get_client()
        resp = await client.request(
            "POST", f"/workspaces/{workspace_id}/docs/{params.doc_id}/pages", json_body=body, use_v3=True
        )
        payload = resp.json()
        page = payload.get("page") if isinstance(payload, dict) and "page" in payload else payload
        page = page if isinstance(page, dict) else {}
        new_id = page.get("id", "?")
        return f"Created page **{params.name}** (id `{new_id}`) in Doc `{params.doc_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_get_page",
    annotations=ToolAnnotations(
        title="Get ClickUp Doc Page",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_get_page(params: GetPageInput) -> str:
    """Fetch one page of a Doc, including its content.

    When to Use:
    - To read or re-read a single page by id (e.g. before editing it).

    When NOT to Use:
    - To read every page — use `clickup_get_doc_pages`.

    Returns:
    The page's title, subtitle, and content (markdown by default).

    Examples:
    - params = {"doc_id": "8cbq...", "page_id": "abc"}

    Error Handling:
    404 → page not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        query: dict[str, Any] = {"content_format": params.content_format.value}
        client = get_client()
        resp = await client.request(
            "GET",
            f"/workspaces/{workspace_id}/docs/{params.doc_id}/pages/{params.page_id}",
            params=query,
            use_v3=True,
        )
        payload = resp.json()
        page = payload.get("page") if isinstance(payload, dict) and "page" in payload else payload
        page = page if isinstance(page, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return _cap(to_json(page))
        return _cap(_format_page(page))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_edit_page",
    annotations=ToolAnnotations(
        title="Edit ClickUp Doc Page",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_edit_page(params: EditPageInput) -> str:
    """Update a Doc page's title, subtitle, and/or content.

    `content_edit_mode` controls how `content` is applied: `replace` (default)
    overwrites the page body, `append` adds to the end, `prepend` adds to the
    start — handy for logging into an existing page without a read-modify-write.

    When to Use:
    - To rename a page, change its subtitle, or overwrite/append/prepend content.

    When NOT to Use:
    - To create a new page — use `clickup_create_page`.

    Returns:
    A confirmation naming the page, Doc, and edit mode used.

    Examples:
    - params = {"doc_id": "d", "page_id": "p", "content": "new body"}
    - params = {"doc_id": "d", "page_id": "p", "content": "- log", "content_edit_mode": "append"}

    Error Handling:
    404 → page not found. Errors return an `Error ...` string.
    """
    try:
        workspace_id = _resolve_workspace_id(params.workspace_id)
        body: dict[str, Any] = {
            "content_edit_mode": params.content_edit_mode.value,
            "content_format": params.content_format.value,
        }
        if params.name is not None:
            body["name"] = params.name
        if params.sub_title is not None:
            body["sub_title"] = params.sub_title
        if params.content is not None:
            body["content"] = params.content

        client = get_client()
        await client.request(
            "PUT",
            f"/workspaces/{workspace_id}/docs/{params.doc_id}/pages/{params.page_id}",
            json_body=body,
            use_v3=True,
        )
        return f"Updated page `{params.page_id}` in Doc `{params.doc_id}` (mode: {params.content_edit_mode.value})."
    except Exception as exc:
        return handle_api_error(exc)
