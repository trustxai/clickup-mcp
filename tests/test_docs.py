"""Unit tests for the ClickUp Docs v3 tools against a fake client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from clickup_mcp.tools.docs import (
    CreateDocInput,
    CreatePageInput,
    DocPageListingInput,
    DocPagesInput,
    EditPageInput,
    GetDocInput,
    GetPageInput,
    SearchDocsInput,
    clickup_create_doc,
    clickup_create_page,
    clickup_edit_page,
    clickup_get_doc,
    clickup_get_doc_page_listing,
    clickup_get_doc_pages,
    clickup_get_page,
    clickup_search_docs,
)

WS = "9001"


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("clickup_mcp.tools.docs.get_client", lambda: fake)


# --------------------------------------------------------------------------- search
async def test_search_docs_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"docs": [{"id": "d1", "name": "Spec", "parent": {"id": "s1", "type": 4}}]})
    _patch(monkeypatch, fake)

    result = await clickup_search_docs(SearchDocsInput(workspace_id=WS, parent_id="s1", parent_type="space"))

    assert "Spec" in result and "d1" in result
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == f"/workspaces/{WS}/docs"
    assert call["use_v3"] is True
    assert call["params"]["parent_id"] == "s1"
    # friendly enum maps to the API's upper-case parent_type filter
    assert call["params"]["parent_type"] == "SPACE"
    assert call["params"]["limit"] == 50


async def test_search_docs_cursor_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"docs": [{"id": "d1", "name": "A"}], "next_cursor": "CUR2"})
    _patch(monkeypatch, fake)

    result = await clickup_search_docs(SearchDocsInput(workspace_id=WS, cursor="CUR1"))

    # inbound cursor is forwarded, outbound next_cursor is surfaced to the caller
    assert fake.calls[0]["params"]["cursor"] == "CUR1"
    assert "CUR2" in result


async def test_search_docs_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[{"id": "d1", "name": "Bare"}])  # bare-array response shape
    _patch(monkeypatch, fake)

    result = await clickup_search_docs(SearchDocsInput(workspace_id=WS, response_format="json"))

    assert '"count": 1' in result
    assert "Bare" in result


# --------------------------------------------------------------------------- create doc
async def test_create_doc_in_location(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "newdoc", "name": "Q3"})
    _patch(monkeypatch, fake)

    result = await clickup_create_doc(
        CreateDocInput(workspace_id=WS, name="Q3", parent_id="L9", parent_type="list")
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{WS}/docs"
    assert call["json_body"]["name"] == "Q3"
    # list -> numeric enum 6, nested under parent object
    assert call["json_body"]["parent"] == {"id": "L9", "type": 6}
    assert call["json_body"]["create_page"] is True
    assert "newdoc" in result and "list" in result


async def test_create_doc_without_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "d0", "name": "Scratch"})
    _patch(monkeypatch, fake)

    result = await clickup_create_doc(CreateDocInput(workspace_id=WS, name="Scratch", create_page=False))

    body = fake.calls[0]["json_body"]
    assert "parent" not in body
    assert body["create_page"] is False
    assert "default location" in result


def test_create_doc_parent_pair_validation() -> None:
    with pytest.raises(ValidationError):
        CreateDocInput(name="x", parent_id="only-id")  # parent_type missing
    with pytest.raises(ValidationError):
        CreateDocInput(name="x", parent_type="space")  # parent_id missing


# --------------------------------------------------------------------------- get doc
async def test_get_doc_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "d1", "name": "Handbook", "parent": {"id": "s1", "type": 4}})
    _patch(monkeypatch, fake)

    result = await clickup_get_doc(GetDocInput(workspace_id=WS, doc_id="d1"))

    assert fake.calls[0]["path"] == f"/workspaces/{WS}/docs/d1"
    assert "Handbook" in result


async def test_get_doc_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    req = httpx.Request("GET", "https://api.clickup.com/api/v3/workspaces/9001/docs/nope")
    resp = httpx.Response(404, json={"err": "Doc not found", "ECODE": "DOC_001"}, request=req)
    fake = _FakeClient(exc=httpx.HTTPStatusError("404", request=req, response=resp))
    _patch(monkeypatch, fake)

    result = await clickup_get_doc(GetDocInput(workspace_id=WS, doc_id="nope"))

    assert result.startswith("Error (404)")
    assert "Doc not found" in result


# --------------------------------------------------------------------------- page listing
async def test_get_doc_page_listing_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"id": "p1", "name": "Root", "pages": [{"id": "p2", "name": "Child"}]}]
    fake = _FakeClient(payload=payload)
    _patch(monkeypatch, fake)

    result = await clickup_get_doc_page_listing(DocPageListingInput(workspace_id=WS, doc_id="d1"))

    # snake_case path, not pageListing
    assert fake.calls[0]["path"] == f"/workspaces/{WS}/docs/d1/page_listing"
    assert "Root" in result and "Child" in result
    # child is indented one level deeper than the root
    assert "  - **Child**" in result


# --------------------------------------------------------------------------- doc pages
async def test_get_doc_pages_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"pages": [{"id": "p1", "name": "Intro", "content": "# hello world"}]})
    _patch(monkeypatch, fake)

    result = await clickup_get_doc_pages(DocPagesInput(workspace_id=WS, doc_id="d1", content_format="text/plain"))

    call = fake.calls[0]
    assert call["path"] == f"/workspaces/{WS}/docs/d1/pages"
    assert call["params"]["content_format"] == "text/plain"
    assert "Intro" in result and "hello world" in result


# --------------------------------------------------------------------------- create page
async def test_create_page_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "pg1", "name": "Overview"})
    _patch(monkeypatch, fake)

    result = await clickup_create_page(
        CreatePageInput(workspace_id=WS, doc_id="d1", name="Overview", content="body", parent_page_id="root")
    )

    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/workspaces/{WS}/docs/d1/pages"
    assert call["json_body"]["name"] == "Overview"
    assert call["json_body"]["content"] == "body"
    assert call["json_body"]["parent_page_id"] == "root"
    assert call["json_body"]["content_format"] == "text/md"
    assert "pg1" in result


# --------------------------------------------------------------------------- get page
async def test_get_page_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "pg1", "name": "Detail", "content": "text here"})
    _patch(monkeypatch, fake)

    result = await clickup_get_page(GetPageInput(workspace_id=WS, doc_id="d1", page_id="pg1"))

    assert fake.calls[0]["path"] == f"/workspaces/{WS}/docs/d1/pages/pg1"
    assert "Detail" in result and "text here" in result


# --------------------------------------------------------------------------- edit page
async def test_edit_page_append(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    _patch(monkeypatch, fake)

    result = await clickup_edit_page(
        EditPageInput(workspace_id=WS, doc_id="d1", page_id="pg1", content="log line", content_edit_mode="append")
    )

    call = fake.calls[0]
    assert call["method"] == "PUT"
    assert call["path"] == f"/workspaces/{WS}/docs/d1/pages/pg1"
    assert call["json_body"]["content_edit_mode"] == "append"
    assert call["json_body"]["content"] == "log line"
    assert "append" in result


def test_edit_page_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        EditPageInput(doc_id="d1", page_id="pg1")  # nothing to change


# --------------------------------------------------------------------------- workspace resolution
async def test_missing_workspace_id_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # env is isolated (no CLICKUP_TEAM_ID) by the autouse conftest fixture
    fake = _FakeClient(payload={"docs": []})
    _patch(monkeypatch, fake)

    result = await clickup_search_docs(SearchDocsInput())

    assert result.startswith("Error")
    assert "Workspace id" in result
    assert fake.calls == []  # never reached the client


# --------------------------------------------------------------------------- live smoke (read-only)
@pytest.mark.live
async def test_search_docs_live() -> None:
    result = await clickup_search_docs(SearchDocsInput(limit=10))
    assert isinstance(result, str)
    assert not result.startswith("Error")
