# ClickUp MCP Server (trustai-clickup-mcp)

A comprehensive [MCP](https://modelcontextprotocol.io) server for the
[ClickUp public API](https://developer.clickup.com/reference) (v2 + v3), built on the
official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (FastMCP,
stdio transport).

> Status: under active construction. The full tool table, client configuration blocks,
> and troubleshooting guide land with the first release.
>
> Note: the PyPI distribution is **`trustai-clickup-mcp`** (the bare `clickup-mcp` name
> is taken by an unrelated package) — always `uvx trustai-clickup-mcp`.

## Features

- Full coverage of the ClickUp public API surface: Spaces, Folders, Lists, statuses
  (via Space/Folder/List payloads), Views, Templates, Tasks, Docs (v3,
  create-in-location), Comments, Checklists, Tags, Custom Fields, Time Tracking 2.0,
  Goals, Members/Guests/User Groups, Webhooks, Chat (v3), and more.
- Dual-format responses (markdown for humans/LLMs, JSON for programmatic use).
- Strict input validation (pydantic v2), uniform error mapping (including ClickUp
  `ECODE`s and 429 rate-limit guidance), stdio-only transport.

## Quickstart

```bash
git clone https://github.com/trustxai/clickup-mcp.git
cd clickup-mcp
uv sync --group dev
cp .env.example .env   # set CLICKUP_API_TOKEN
uv run trustai-clickup-mcp
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLICKUP_API_TOKEN` | yes | — | Personal API token (`pk_…`) or OAuth2 access token |
| `CLICKUP_TEAM_ID` | no | — | Default Workspace id used when a call omits `team_id` |
| `CLICKUP_API_URL` | no | `https://api.clickup.com/api/v2` | v2 base URL |
| `CLICKUP_API_URL_V3` | no | `https://api.clickup.com/api/v3` | v3 base URL (Docs, Chat, …) |
| `CLICKUP_REQUEST_TIMEOUT_SECONDS` | no | `30` | Per-request timeout |

## Contributing

Conventional commits drive releases (release-please). Run the full gate before pushing:

```bash
uv run pytest -m "not live" && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/
```

## License

Apache-2.0
