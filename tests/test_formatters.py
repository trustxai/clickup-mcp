"""Unit tests for the shared response formatters."""

from __future__ import annotations

import json

from clickup_mcp.formatters import ResponseFormat, epoch_to_human, paginated_response, to_json


def test_epoch_to_human_milliseconds() -> None:
    assert epoch_to_human(1720000000000) == "2024-07-03 09:46:40 UTC"


def test_epoch_to_human_string_milliseconds() -> None:
    # ClickUp serializes date fields as strings.
    assert epoch_to_human("1720000000000") == "2024-07-03 09:46:40 UTC"


def test_epoch_to_human_seconds_autodetected() -> None:
    assert epoch_to_human(1720000000) == "2024-07-03 09:46:40 UTC"


def test_epoch_to_human_empty() -> None:
    assert epoch_to_human(None) == "N/A"
    assert epoch_to_human("") == "N/A"


def test_epoch_to_human_non_numeric_passthrough() -> None:
    assert epoch_to_human("not-a-date") == "not-a-date"


def test_to_json_stable() -> None:
    assert json.loads(to_json({"a": 1})) == {"a": 1}


def _fmt(item: dict[str, object]) -> str:
    return f"- **{item['name']}**"


def test_paginated_response_markdown_with_total() -> None:
    output = paginated_response(
        items=[{"name": "Space A"}],
        total=10,
        limit=1,
        offset=0,
        fmt=ResponseFormat.MARKDOWN,
        item_formatter=_fmt,
        title="Spaces",
    )
    assert "# Spaces" in output
    assert "total **10**" in output
    assert "next offset → **1**" in output
    assert "- **Space A**" in output


def test_paginated_response_markdown_full_page_has_more() -> None:
    output = paginated_response(
        items=[{"name": "A"}, {"name": "B"}],
        limit=2,
        offset=0,
        fmt=ResponseFormat.MARKDOWN,
        item_formatter=_fmt,
        title="Things",
    )
    assert "More available" in output


def test_paginated_response_json() -> None:
    output = paginated_response(
        items=[{"name": "A"}],
        limit=20,
        offset=0,
        fmt=ResponseFormat.JSON,
        item_formatter=_fmt,
        title="Things",
    )
    payload = json.loads(output)
    assert payload["title"] == "Things"
    assert payload["count"] == 1
    assert payload["has_more"] is False
    assert payload["items"] == [{"name": "A"}]


def test_paginated_response_empty_markdown() -> None:
    output = paginated_response(
        items=[],
        limit=20,
        offset=0,
        fmt=ResponseFormat.MARKDOWN,
        item_formatter=_fmt,
        title="Nothing",
    )
    assert "_No items._" in output
