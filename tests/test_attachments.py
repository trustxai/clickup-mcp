"""Unit tests for the attachment tools against a fake client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.formatters import ResponseFormat
from clickup_mcp.tools.attachments import (
    CreateEntityAttachmentInput,
    CreateTaskAttachmentInput,
    GetEntityAttachmentsInput,
    clickup_create_entity_attachment,
    clickup_create_task_attachment,
    clickup_get_entity_attachments,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.attachments.get_client", lambda: fake)


def _make_file(tmp_path: Path, name: str = "report.txt", content: bytes = b"hello") -> str:
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


async def test_create_task_attachment_happy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeClient(payload={"id": "att-1", "url": "https://cdn.clickup.com/x", "title": "report.txt"})
    _patch(monkeypatch, fake)
    file_path = _make_file(tmp_path)

    result = await clickup_create_task_attachment(CreateTaskAttachmentInput(task_id="abc", file_path=file_path))

    assert "att-1" in result
    assert "https://cdn.clickup.com/x" in result
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/task/abc/attachment"
    # multipart file field must be named "attachment" and carry the real bytes
    assert "attachment" in call["files"]
    assert call["files"]["attachment"] == ("report.txt", b"hello")
    assert call["params"] is None


async def test_create_task_attachment_filename_override_and_custom_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeClient(payload={"id": "att-2", "url": "https://cdn/y"})
    _patch(monkeypatch, fake)
    file_path = _make_file(tmp_path, name="raw.bin", content=b"\x00\x01")

    await clickup_create_task_attachment(
        CreateTaskAttachmentInput(
            task_id="PROJ-1", file_path=file_path, filename="nice.bin", custom_task_ids=True, team_id="123"
        )
    )

    call = fake.calls[0]
    assert call["files"]["attachment"] == ("nice.bin", b"\x00\x01")
    assert call["params"] == {"custom_task_ids": "true", "team_id": "123"}


async def test_create_task_attachment_missing_file() -> None:
    with pytest.raises(ValidationError):
        CreateTaskAttachmentInput(task_id="abc", file_path="/no/such/file.txt")


async def test_create_task_attachment_size_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Shrink the cap so a tiny file trips it, then confirm validation rejects it.
    monkeypatch.setattr("clickup_mcp.tools.attachments.MAX_ATTACHMENT_BYTES", 2)
    file_path = _make_file(tmp_path, content=b"too big")
    with pytest.raises(ValidationError):
        CreateTaskAttachmentInput(task_id="abc", file_path=file_path)


async def test_get_entity_attachments_markdown_with_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "attachments": [
                {"id": "a1", "title": "spec.pdf", "url": "https://cdn/a1", "size": 2048, "date_created": "0"}
            ],
            "next_cursor": "CUR2",
        }
    )
    _patch(monkeypatch, fake)

    result = await clickup_get_entity_attachments(
        GetEntityAttachmentsInput(workspace_id="123", entity_type="attachments", entity_id="abc")
    )

    assert "spec.pdf" in result
    assert "a1" in result
    assert "cursor=CUR2" in result
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/workspaces/123/attachments/abc/attachments"
    assert call["use_v3"] is True
    assert call["params"] == {"limit": 50}


async def test_get_entity_attachments_json_and_cursor_param(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[{"id": "a1", "title": "x", "url": "u"}])  # bare-array response
    _patch(monkeypatch, fake)

    result = await clickup_get_entity_attachments(
        GetEntityAttachmentsInput(
            workspace_id="123",
            entity_type="custom_fields",
            entity_id="fld-1",
            cursor="CUR1",
            limit=10,
            response_format=ResponseFormat.JSON,
        )
    )

    assert '"attachments"' in result
    assert '"a1"' in result
    call = fake.calls[0]
    assert call["path"] == "/workspaces/123/custom_fields/fld-1/attachments"
    assert call["params"] == {"limit": 10, "cursor": "CUR1"}


async def test_create_entity_attachment_v3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeClient(payload={"id": "att-9", "url": "https://cdn/9", "title": "a.png"})
    _patch(monkeypatch, fake)
    file_path = _make_file(tmp_path, name="a.png", content=b"img")

    result = await clickup_create_entity_attachment(
        CreateEntityAttachmentInput(
            workspace_id="123", entity_type="custom_fields", entity_id="fld-1", file_path=file_path, filename="a.png"
        )
    )

    assert "att-9" in result
    assert "custom_fields" in result
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/workspaces/123/custom_fields/fld-1/attachments"
    assert call["use_v3"] is True
    assert call["files"]["attachment"] == ("a.png", b"img")
    assert call["data"] == {"filename": "a.png"}


async def test_get_entity_attachments_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.clickup.com/api/v3/workspaces/123/attachments/abc/attachments")
    response = httpx.Response(404, json={"err": "not found", "ECODE": "ATTCH_001"}, request=request)
    fake = _FakeClient(exc=httpx.HTTPStatusError("404", request=request, response=response))
    _patch(monkeypatch, fake)

    result = await clickup_get_entity_attachments(
        GetEntityAttachmentsInput(workspace_id="123", entity_type="attachments", entity_id="abc")
    )

    assert result.startswith("Error (404)")


@pytest.mark.live
async def test_get_entity_attachments_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read-only smoke test; skipped unless CLICKUP_API_TOKEN + a test task id are set."""
    import os

    from clickup_mcp.tools.attachments import clickup_get_entity_attachments as live_tool

    workspace_id = os.environ.get("CLICKUP_TEAM_ID")
    task_id = os.environ.get("CLICKUP_TEST_TASK_ID")
    if not workspace_id or not task_id:
        pytest.skip("live attachment read needs CLICKUP_TEAM_ID and CLICKUP_TEST_TASK_ID")

    result = await live_tool(
        GetEntityAttachmentsInput(workspace_id=workspace_id, entity_type="attachments", entity_id=task_id)
    )
    assert isinstance(result, str)
    assert "Attachments for" in result or result.startswith("Error")
