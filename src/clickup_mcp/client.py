"""Async HTTP client for the ClickUp public API (v2 and v3 surfaces)."""

from __future__ import annotations

from typing import Any

import httpx

from clickup_mcp.config import Settings, get_settings


class ClickUpClient:
    """Thin async wrapper around httpx for the ClickUp API.

    ClickUp auth is a static token sent verbatim in the ``Authorization`` header
    (personal ``pk_...`` tokens and OAuth2 access tokens both work this way), so
    there is no token-exchange lifecycle. Most endpoints live under ``/api/v2``;
    Docs, Chat, entity attachments, and ACL live under ``/api/v3`` — select the
    base with ``use_v3=True``.
    """

    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings or get_settings()
        # Injectable transport so tests can use httpx.MockTransport.
        self._transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        use_v3: bool = False,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform an authenticated request and return the response.

        Raises `httpx.HTTPStatusError` on non-2xx (tools route it through
        `clickup_mcp.errors.handle_api_error`) and `RuntimeError` when no
        token is configured.
        """
        settings = self._settings
        if not settings.has_token:
            raise RuntimeError(
                "No ClickUp credentials configured. Set CLICKUP_API_TOKEN in the environment or .env "
                "(ClickUp → Settings → Apps → API Token)."
            )

        base = settings.clickup_api_url_v3 if use_v3 else settings.clickup_api_url
        url = f"{base.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "accept": "application/json",
            "authorization": settings.clickup_api_token,
        }
        effective_timeout = timeout if timeout is not None else settings.clickup_request_timeout_seconds

        async with httpx.AsyncClient(timeout=effective_timeout, transport=self._transport) as client:
            resp = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                data=data,
                files=files,
                headers=headers,
            )
        resp.raise_for_status()
        return resp


_client: ClickUpClient | None = None


def get_client() -> ClickUpClient:
    """Lazy module-level singleton (tools monkeypatch this in unit tests)."""
    global _client
    if _client is None:
        _client = ClickUpClient()
    return _client
