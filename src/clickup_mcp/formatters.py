"""Response formatting shared by every tool (markdown / JSON dual output)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ResponseFormat(StrEnum):
    """Output format selector present on list/get tools."""

    MARKDOWN = "markdown"
    JSON = "json"


def to_json(data: Any) -> str:
    """Serialize any payload for the LLM (stable, human-readable)."""
    return json.dumps(data, indent=2, default=str)


def epoch_to_human(value: Any) -> str:
    """Render a ClickUp timestamp as UTC ISO-8601.

    ClickUp date fields are unix epochs in *milliseconds* (often serialized as
    strings); second-resolution epochs are auto-detected and handled too.
    """
    if value in (None, "", 0, "0"):
        return "N/A"
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return str(value)
    if epoch > 1e11:  # milliseconds
        epoch /= 1000.0
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def paginated_response(
    *,
    items: list[dict[str, Any]],
    limit: int,
    offset: int,
    fmt: ResponseFormat,
    item_formatter: Callable[[dict[str, Any]], str],
    title: str,
    total: int | None = None,
) -> str:
    """Uniform paginated output for list tools.

    `has_more` is computed from `total` when the API provides one, otherwise
    from the page being full (`len(items) == limit`).
    """
    count = len(items)
    if total is not None:
        has_more = total > offset + count
    else:
        has_more = count == limit

    if fmt is ResponseFormat.JSON:
        return to_json(
            {
                "title": title,
                "count": count,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "items": items,
            }
        )

    lines = [f"# {title}", ""]
    if total is not None:
        lines.append(f"Showing **{count:,}** of total **{total:,}** (offset {offset:,}).")
    else:
        lines.append(f"Showing **{count:,}** item(s) (offset {offset:,}).")
    if has_more:
        lines.append(f"More available — next offset → **{offset + count:,}**.")
    lines.append("")
    for item in items:
        lines.append(item_formatter(item))
    if not items:
        lines.append("_No items._")
    return "\n".join(lines)
