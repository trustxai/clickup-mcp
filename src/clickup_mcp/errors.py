"""Uniform error-to-string mapping for tool responses."""

from __future__ import annotations

import httpx


def _detail_from_body(resp: httpx.Response) -> str:
    """Extract ClickUp's error detail (`err` + `ECODE`) from a response body."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(body, dict):
        err = body.get("err") or body.get("error") or body.get("message") or ""
        ecode = body.get("ECODE") or body.get("ecode") or ""
        if err and ecode:
            return f"{err} (ECODE {ecode})"
        if err:
            return str(err)
    return resp.text[:300]


def handle_api_error(exc: Exception) -> str:
    """Map an exception to a human-readable `Error ...` string for the LLM.

    Tools never raise: every tool body is wrapped in try/except and returns
    this string on failure.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = _detail_from_body(exc.response)
        if status == 400:
            return f"Error (400): Bad request – {detail}. Double-check parameter values and payload shapes."
        if status == 401:
            return (
                f"Error (401): Unauthorized – {detail}. The CLICKUP_API_TOKEN is missing, invalid, or revoked; "
                "generate one in ClickUp → Settings → Apps → API Token."
            )
        if status == 403:
            return (
                f"Error (403): Forbidden – {detail}. You lack access to this resource, or the endpoint requires "
                "a higher ClickUp plan (several Guest/User/Audit endpoints are Enterprise-only)."
            )
        if status == 404:
            return f"Error (404): Not found – {detail}. Double-check the id (task_id, list_id, space_id, …)."
        if status == 429:
            return (
                f"Error (429): Rate limited – {detail}. ClickUp allows ~100 requests/minute per token; "
                "wait before retrying and batch reads where possible."
            )
        return f"Error ({status}): {detail}"
    if isinstance(exc, httpx.TimeoutException):
        return "Error: the ClickUp API request timed out. Try again or raise CLICKUP_REQUEST_TIMEOUT_SECONDS."
    if isinstance(exc, httpx.ConnectError):
        return "Error: could not connect to the ClickUp API. Check network access and CLICKUP_API_URL."
    if isinstance(exc, RuntimeError):
        return f"Error: {exc}"
    return f"Error: unexpected failure – {type(exc).__name__}: {exc}"
