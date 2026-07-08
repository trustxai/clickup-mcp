# ClickUp MCP Server (amazing-clickup-mcp)

A comprehensive [Model Context Protocol](https://modelcontextprotocol.io) server for the
[ClickUp public API](https://developer.clickup.com/reference) (v2 + v3), built on the
official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (FastMCP,
stdio transport). It exposes **166 tools** covering nearly the entire ClickUp surface —
Spaces, Folders, Lists, Views, Docs, Tasks, Comments, Checklists, Tags, Custom Fields,
Time Tracking, Goals, Members, Guests, Webhooks, Chat, and more — so an LLM can read and
drive a ClickUp Workspace on your behalf.

> The PyPI distribution and console script are both **`amazing-clickup-mcp`** (the bare
> `clickup-mcp` name is taken on PyPI by an unrelated package). Always run
> `uvx amazing-clickup-mcp` — never `uvx clickup-mcp`.

## Features

- **Full API coverage (166 tools).** v2 and v3 endpoints across 22 resource groups —
  Spaces, Folders, Lists, Views, Docs, Tasks, Comments, Checklists, Tags, Custom Fields,
  Time Tracking 2.0, Relationships, Attachments, Goals, Members & User Groups, Guests,
  Users, Webhooks, Workspace admin, Chat channels, Chat messages, and a health check.
- **Statuses managed via payloads.** ClickUp has no dedicated statuses endpoint; create
  and update Space/Folder/List objects with `statuses`/`override_statuses` payloads.
- **Docs created in-place (v3).** Create a Doc directly in its final Space/Folder/List/
  Everything location — no drag-later.
- **Dual-format responses.** Markdown for humans and LLMs (default) or JSON for
  programmatic use, selectable per call via `response_format`.
- **Strict input validation.** One pydantic v2 model per tool (`extra="forbid"`,
  field-level constraints) so malformed calls fail fast with a clear message.
- **Uniform error mapping.** 400/401/403/404/429, timeouts, and connection errors are
  normalized into readable strings that surface ClickUp `err`/`ECODE` details and
  rate-limit guidance.
- **stdio-only transport.** No HTTP/SSE and no logging to stdout — the standard input/
  output channel is the MCP protocol channel.

## Available Tools

All 166 tools, grouped by resource. Read tools return either markdown (default) or JSON;
mutating tools return a human-readable confirmation.

| Tool | Description |
|---|---|
| **Spaces** | |
| `clickup_create_space` | Create a new Space inside a Workspace. |
| `clickup_get_spaces` | List the Spaces in a Workspace. |
| `clickup_get_space` | Fetch full detail for a single Space, including its status workflow and ClickApp toggles. |
| `clickup_update_space` | Rename a Space, change its color/privacy, or replace its ClickApp/status configuration. |
| `clickup_delete_space` | Permanently delete a Space and everything inside it. |
| **Folders** | |
| `clickup_create_folder` | Create a Folder inside a Space. |
| `clickup_get_folders` | List the Folders in a Space. |
| `clickup_get_folder` | Fetch one Folder, including its Lists and status workflow. |
| `clickup_update_folder` | Rename a Folder and/or toggle its status-override setting. |
| `clickup_delete_folder` | Permanently delete a Folder and every List/Task inside it. |
| `clickup_create_folder_from_template` | Create a new Folder by replaying a Folder template. |
| `clickup_get_folder_templates` | List the Folder templates available in a Workspace. |
| **Lists** | |
| `clickup_create_list` | Create a new List inside a Folder. |
| `clickup_create_folderless_list` | Create a new List directly inside a Space, with no parent Folder. |
| `clickup_get_lists` | List the Lists that belong to a Folder. |
| `clickup_get_folderless_lists` | List the Lists that live directly inside a Space (no Folder). |
| `clickup_get_list` | Fetch full detail for a single List by id. |
| `clickup_update_list` | Update a List's name, description, dates, priority, assignee, or color. |
| `clickup_delete_list` | Permanently delete a List from the Workspace. |
| `clickup_add_task_to_list` | Add an existing Task's membership to an additional List. |
| `clickup_remove_task_from_list` | Remove a Task's membership from an additional (non-home) List. |
| `clickup_create_list_from_template_in_folder` | Create a new List inside a Folder by instantiating a List template. |
| `clickup_create_list_from_template_in_space` | Create a new folderless List inside a Space by instantiating a template. |
| `clickup_get_list_templates` | List available List templates for a Workspace. |
| **Views** | |
| `clickup_create_team_view` | Create a task or page view at the Everything Level of a Workspace. |
| `clickup_create_space_view` | Create a task or page view scoped to a Space. |
| `clickup_create_folder_view` | Create a task or page view scoped to a Folder. |
| `clickup_create_list_view` | Create a task or page view scoped to a List. |
| `clickup_get_team_views` | List the task and page views defined at the Everything Level of a Workspace. |
| `clickup_get_space_views` | List the task and page views available for a Space. |
| `clickup_get_folder_views` | List the task and page views available for a Folder. |
| `clickup_get_list_views` | List the task and page views available for a List. |
| `clickup_get_view` | Get the full configuration of a single task or page view. |
| `clickup_get_view_tasks` | List the tasks currently visible in a view, honoring its filters and sorting. |
| `clickup_update_view` | Rename a view and/or replace its grouping/sorting/filters/columns/settings. |
| `clickup_delete_view` | Permanently delete a task or page view. |
| **Docs** | |
| `clickup_search_docs` | Search the Docs in a Workspace, optionally filtered by parent location. |
| `clickup_create_doc` | Create a Doc, optionally placed directly in its final location. |
| `clickup_get_doc` | Fetch a single Doc's metadata (name, parent, timestamps). |
| `clickup_get_doc_page_listing` | List a Doc's page tree (titles and ids only, nested — no content). |
| `clickup_get_doc_pages` | Fetch a Doc's pages with content (the full readable body of the Doc). |
| `clickup_create_page` | Add a page to an existing Doc (optionally nested under another page). |
| `clickup_get_page` | Fetch one page of a Doc, including its content. |
| `clickup_edit_page` | Update a Doc page's title, subtitle, and/or content. |
| **Tasks** | |
| `clickup_create_task` | Create a task inside a List. |
| `clickup_get_task` | Fetch one task by id. |
| `clickup_get_tasks` | List tasks inside ONE List, with filters and sorting. |
| `clickup_update_task` | Update fields on an existing task. |
| `clickup_delete_task` | Permanently delete a task. |
| `clickup_get_filtered_team_tasks` | Search tasks across the ENTIRE Workspace with rich filters. |
| `clickup_move_task` | Move a task to a new home List. |
| `clickup_merge_tasks` | Merge one or more source tasks into a target task. |
| `clickup_create_task_from_template` | Create a task in a List from a saved task template. |
| `clickup_get_task_templates` | List the Workspace's saved task templates. |
| `clickup_get_task_time_in_status` | Report how long one task has spent in each status. |
| `clickup_get_bulk_tasks_time_in_status` | Report time-in-status for 2 to 100 tasks in one call. |
| **Comments** | |
| `clickup_create_task_comment` | Add a comment to a task. |
| `clickup_get_task_comments` | List comments on a task, newest first. |
| `clickup_create_list_comment` | Add a comment to a List's info panel. |
| `clickup_get_list_comments` | List comments on a List's info panel, newest first. |
| `clickup_create_chat_view_comment` | Post a comment into a Chat view. |
| `clickup_get_chat_view_comments` | List comments in a Chat view, newest first. |
| `clickup_update_comment` | Edit a comment's text, (re)assign it, or toggle its resolved state. |
| `clickup_delete_comment` | Permanently delete a comment. |
| `clickup_create_threaded_comment` | Reply inside an existing comment's thread. |
| `clickup_get_threaded_comments` | List the replies in a comment's thread. |
| **Checklists** | |
| `clickup_create_checklist` | Add a new (empty) checklist to a task. |
| `clickup_edit_checklist` | Rename a checklist and/or reposition it among a task's other checklists. |
| `clickup_delete_checklist` | Permanently remove a checklist (and all of its items) from its task. |
| `clickup_create_checklist_item` | Add a new line item to an existing checklist. |
| `clickup_edit_checklist_item` | Rename, (un)resolve, reassign, or nest/un-nest a checklist item. |
| `clickup_delete_checklist_item` | Remove a single line item from a checklist (the checklist itself stays). |
| **Tags** | |
| `clickup_get_space_tags` | List every task Tag defined in a Space, with its foreground/background colors. |
| `clickup_create_space_tag` | Add a new task Tag (with optional colors) to a Space's tag palette. |
| `clickup_edit_space_tag` | Rename and/or recolor an existing Space tag. |
| `clickup_delete_space_tag` | Remove a tag from a Space's palette (and from every task carrying it). |
| `clickup_add_tag_to_task` | Apply an existing Space tag to a task. |
| `clickup_remove_tag_from_task` | Remove a tag from a task without deleting the tag from the Space. |
| **Custom Fields** | |
| `clickup_get_list_custom_fields` | List the Custom Fields accessible from a List. |
| `clickup_get_folder_custom_fields` | List the Custom Fields available at a Folder scope. |
| `clickup_get_space_custom_fields` | List the Custom Fields available at a Space scope. |
| `clickup_get_team_custom_fields` | List the Workspace-scoped Custom Fields. |
| `clickup_set_custom_field_value` | Set a Custom Field's value on a task. |
| `clickup_remove_custom_field_value` | Clear a Custom Field's value on a task. |
| `clickup_get_custom_task_types` | List the Workspace's custom task types (a.k.a. custom items). |
| **Time Tracking** | |
| `clickup_get_time_entries` | List time entries in a Workspace, optionally scoped by date range and location. |
| `clickup_get_time_entry` | Fetch one time entry by id. |
| `clickup_get_time_entry_history` | View the list of changes made to a time entry. |
| `clickup_get_running_time_entry` | Get the currently running time entry for a user, if any. |
| `clickup_create_time_entry` | Log a completed (already-finished) time entry. |
| `clickup_update_time_entry` | Edit an existing time entry's description, tags, times, task, or billable flag. |
| `clickup_delete_time_entry` | Permanently delete one or more time entries. |
| `clickup_start_time_entry` | Start a new, open-ended running timer for the authenticated user. |
| `clickup_stop_time_entry` | Stop the authenticated user's currently running timer. |
| `clickup_get_time_entry_tags` | List every tag ever applied to a time entry in this Workspace. |
| `clickup_add_time_entry_tags` | Apply one or more tags to one or more time entries. |
| `clickup_remove_time_entry_tags` | Remove one or more tags from one or more time entries. |
| `clickup_rename_time_entry_tag` | Rename a time-entry tag (and set its colors) across the whole Workspace. |
| `clickup_replace_time_estimates_by_user` | Overwrite a task's entire set of per-assignee time estimates. |
| `clickup_update_time_estimates_by_user` | Set or adjust specific assignees' time estimates on a task, leaving others unchanged. |
| **Relationships** | |
| `clickup_add_dependency` | Create a blocking dependency between two tasks. |
| `clickup_delete_dependency` | Remove a blocking dependency edge between two tasks. |
| `clickup_add_task_link` | Link two tasks with a non-blocking "related to" association. |
| `clickup_delete_task_link` | Remove a "related to" link between two tasks. |
| **Attachments** | |
| `clickup_create_task_attachment` | Upload a local file to a task as an attachment (v2). |
| `clickup_get_entity_attachments` | List the attachments of a task or File-type Custom Field (v3). |
| `clickup_create_entity_attachment` | Upload a local file to a task or File-type Custom Field (v3). |
| **Goals** | |
| `clickup_create_goal` | Create a new Goal in a Workspace. |
| `clickup_get_goals` | List the Goals in a Workspace, including any Goal Folders. |
| `clickup_get_goal` | Get a single Goal's full detail, including its Key Results. |
| `clickup_update_goal` | Update an existing Goal's name, due date, description, owners, or color. |
| `clickup_delete_goal` | Permanently delete a Goal and all of its Key Results. |
| `clickup_create_key_result` | Add a Key Result (Target) to a Goal. |
| `clickup_edit_key_result` | Update a Key Result's progress, note, name, owners, or task/list links. |
| `clickup_delete_key_result` | Permanently delete a Key Result (Target) from its Goal. |
| **Members & Groups** | |
| `clickup_get_list_members` | List the Workspace members with direct access to a List. |
| `clickup_get_task_members` | List the Workspace members with direct access to a Task. |
| `clickup_create_user_group` | Create a User Group (ClickUp's endpoint calls this a "Team") in a Workspace. |
| `clickup_get_user_groups` | List the User Groups in a Workspace (ClickUp's endpoint slug: "getteams1"). |
| `clickup_update_user_group` | Rename a User Group and/or add/remove its members. |
| `clickup_delete_user_group` | Permanently delete a User Group from a Workspace. |
| `clickup_get_custom_roles` | List the Custom Roles configured for a Workspace. |
| `clickup_get_shared_hierarchy` | List the Tasks, Lists, and Folders individually shared with the caller. |
| **Guests** (Enterprise) | |
| `clickup_invite_guest_to_workspace` | Invite an external guest to a Workspace by email. |
| `clickup_edit_guest_on_workspace` | Update an existing guest's permission flags or custom role on a Workspace. |
| `clickup_get_guest` | Look up a guest's permission flags and what has been shared with them. |
| `clickup_remove_guest_from_workspace` | Revoke a guest's access to an entire Workspace. |
| `clickup_add_guest_to_task` | Share a single task with an existing guest at a given permission level. |
| `clickup_remove_guest_from_task` | Revoke a guest's access to a single task. |
| `clickup_add_guest_to_list` | Share a List with an existing guest at a given permission level. |
| `clickup_remove_guest_from_list` | Revoke a guest's access to a List. |
| `clickup_add_guest_to_folder` | Share a Folder with an existing guest at a given permission level. |
| `clickup_remove_guest_from_folder` | Revoke a guest's access to a Folder. |
| **Users** (Enterprise) | |
| `clickup_invite_user_to_workspace` | Invite a full member to a Workspace by email. |
| `clickup_edit_user_on_workspace` | Update a Workspace member's username, admin flag, or custom role. |
| `clickup_get_user` | Look up a single Workspace member's profile, role, and admin status. |
| `clickup_remove_user_from_workspace` | Deactivate a full member's access to a Workspace. |
| **Webhooks** | |
| `clickup_create_webhook` | Register a webhook that pushes ClickUp events to your endpoint. |
| `clickup_get_webhooks` | List the webhooks registered in a Workspace, with their delivery health. |
| `clickup_update_webhook` | Change a webhook's endpoint, event subscription, or delivery status. |
| `clickup_delete_webhook` | Permanently delete a webhook, stopping all event delivery to its endpoint. |
| **Workspace** | |
| `clickup_get_workspace_plan` | Report the current subscription plan of a Workspace. |
| `clickup_get_workspace_seats` | Report used, total, and available member and guest seats for a Workspace. |
| `clickup_query_audit_logs` | Query a Workspace's audit trail (Enterprise, Workspace owner only). |
| `clickup_update_privacy_and_access` | Set the privacy of an object and grant/revoke user or group access (Enterprise). |
| **Chat Channels** | |
| `clickup_get_chat_channels` | List the Chat channels in a Workspace, with descriptor filters. |
| `clickup_create_chat_channel` | Create a Workspace-level Chat channel by name. |
| `clickup_create_location_chat_channel` | Create a Chat channel bound to a Space, Folder, or List. |
| `clickup_create_direct_message` | Create (or return) a direct-message Chat channel with up to 15 users. |
| `clickup_get_chat_channel` | Fetch a single Chat channel's metadata by id. |
| `clickup_update_chat_channel` | Update a Chat channel's name, description, topic, visibility, or location. |
| `clickup_delete_chat_channel` | Permanently delete a Chat channel (Channel, DM, or location-bound). |
| `clickup_get_chat_channel_followers` | List the users following a Chat channel. |
| `clickup_get_chat_channel_members` | List the users who are members of a Chat channel. |
| **Chat Messages** | |
| `clickup_get_chat_messages` | List the messages in a Chat channel (newest first), cursor-paginated. |
| `clickup_get_chat_message_replies` | List the replies threaded under a Chat message, cursor-paginated. |
| `clickup_get_chat_message_reactions` | List the emoji reactions on a Chat message, cursor-paginated. |
| `clickup_get_chat_message_tagged_users` | List the users @-tagged (mentioned) in a Chat message, cursor-paginated. |
| `clickup_get_chat_subtypes` | List a Workspace's post subtype IDs (Announcement, Discussion, Idea, Update). |
| `clickup_send_chat_message` | Post a new message to a Chat channel. |
| `clickup_send_chat_reply` | Post a threaded reply under an existing Chat message. |
| `clickup_update_chat_message` | Edit a Chat message's content, assignee, or resolved state. |
| `clickup_delete_chat_message` | Permanently delete a Chat message (and its replies). |
| `clickup_add_chat_reaction` | Add an emoji reaction to a Chat message. |
| `clickup_delete_chat_reaction` | Remove an emoji reaction from a Chat message. |
| **Health** | |
| `clickup_health_check` | Verify connectivity and authentication against the ClickUp API. |

## Prerequisites

- **Python 3.13+** (only needed for the clone/manual path — `uvx` and Docker bring their
  own runtime).
- **[uv](https://docs.astral.sh/uv/)** — used for both the zero-install `uvx` path and
  local development.
- **A ClickUp API token.** A personal token (Settings → Apps → API Token, starts with
  `pk_`) or an OAuth2 access token. See [Authentication](#authentication).
- **(Optional) Your Workspace (team) id** if you want tools to default `team_id` without
  passing it every call.
- **(Optional) Docker** if you prefer the container path.

## Quickstart

```bash
git clone https://github.com/trustxai/clickup-mcp.git
cd clickup-mcp
uv sync --group dev
cp .env.example .env          # then set CLICKUP_API_TOKEN (and optionally CLICKUP_TEAM_ID)
uv run amazing-clickup-mcp    # starts the stdio server
```

The server speaks MCP over stdio, so it is normally launched by an MCP client (see
[Client Configuration](#client-configuration)) rather than run by hand — but launching it
directly is a quick way to confirm it imports and starts.

## Run with uvx (zero install)

No clone, no virtualenv — `uvx` fetches and runs the published package in one step:

```bash
CLICKUP_API_TOKEN=pk_your_token_here uvx amazing-clickup-mcp
```

> Use `amazing-clickup-mcp`, not `clickup-mcp`. The bare `clickup-mcp` name on PyPI is an
> unrelated squatter package and will **not** run this server.

## Client Configuration

Every client launches the server as a subprocess and passes credentials through `env`.
Replace `pk_your_token_here` with your token and, optionally, `your_workspace_id` with
your Workspace (team) id.

### Cursor

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project):

```json
{
  "mcpServers": {
    "clickup": {
      "command": "uvx",
      "args": ["amazing-clickup-mcp"],
      "env": {
        "CLICKUP_API_TOKEN": "pk_your_token_here",
        "CLICKUP_TEAM_ID": "your_workspace_id"
      }
    }
  }
}
```

### Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config; on macOS it lives
at `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "clickup": {
      "command": "uvx",
      "args": ["amazing-clickup-mcp"],
      "env": {
        "CLICKUP_API_TOKEN": "pk_your_token_here",
        "CLICKUP_TEAM_ID": "your_workspace_id"
      }
    }
  }
}
```

Restart Claude Desktop after saving.

### Claude Code

Register the server with a single command:

```bash
claude mcp add clickup \
  --env CLICKUP_API_TOKEN=pk_your_token_here \
  --env CLICKUP_TEAM_ID=your_workspace_id \
  -- uvx amazing-clickup-mcp
```

Or add the equivalent block to `~/.claude.json` under `mcpServers` (same shape as the
Cursor example above).

### MCP Inspector

Exercise the server interactively with the official inspector:

```bash
CLICKUP_API_TOKEN=pk_your_token_here \
  npx @modelcontextprotocol/inspector uvx amazing-clickup-mcp
```

If you cloned the repo, you can instead run `uv run mcp dev src/clickup_mcp/server.py`
(the `mcp` dev CLI ships in the dev dependency group).

### Docker

Build the image from the repo `Dockerfile`, then point any client at `docker run`:

```bash
docker build -t amazing-clickup-mcp .
```

```json
{
  "mcpServers": {
    "clickup": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "CLICKUP_API_TOKEN",
        "-e", "CLICKUP_TEAM_ID",
        "amazing-clickup-mcp"
      ],
      "env": {
        "CLICKUP_API_TOKEN": "pk_your_token_here",
        "CLICKUP_TEAM_ID": "your_workspace_id"
      }
    }
  }
}
```

The `-i` flag is required — the server communicates over stdin/stdout. The `-e NAME`
entries forward the client-provided `env` values into the container.

## Authentication

The server authenticates with a single ClickUp token, sent verbatim in the
`Authorization` header on every request:

- **Personal API token** — ClickUp → Settings → **Apps** → **API Token**. Starts with
  `pk_`. Simplest option; scoped to your own account and permissions.
- **OAuth2 access token** — for a multi-user integration. Supply the access token you
  obtained from the OAuth flow.

The token is read from `CLICKUP_API_TOKEN` (via `.env` or the client's `env` block). It is
never logged. Missing or invalid credentials fail lazily at the first API call with a
clear `401` message rather than at startup.

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLICKUP_API_TOKEN` | yes | — | Personal API token (`pk_…`) or OAuth2 access token. Sent verbatim in the `Authorization` header. |
| `CLICKUP_TEAM_ID` | no | — | Default Workspace (team) id used when a call omits `team_id`. |
| `CLICKUP_API_URL` | no | `https://api.clickup.com/api/v2` | v2 API base URL. Override only for a proxy or testing. |
| `CLICKUP_API_URL_V3` | no | `https://api.clickup.com/api/v3` | v3 API base URL (Docs, Chat, entity attachments, ACL). |
| `CLICKUP_REQUEST_TIMEOUT_SECONDS` | no | `30` | Per-request HTTP timeout, in seconds. |

All variables are optional at import time (the server starts without them); only
`CLICKUP_API_TOKEN` is required to make a successful API call.

## Running Manually

Once dependencies are installed (`uv sync`), any of these start the same stdio server:

```bash
uv run amazing-clickup-mcp     # console script (recommended)
uv run python -m clickup_mcp    # module entry point
```

With the package installed into the active environment, `amazing-clickup-mcp` and
`python -m clickup_mcp` work without the `uv run` prefix.

## Troubleshooting

- **`429 Too Many Requests` / rate limiting.** ClickUp caps requests at roughly **100
  requests per minute per token**. If you hit a `429`, slow down and retry after a short
  pause; the error message surfaces ClickUp's rate-limit details. Prefer bulk/filtered
  tools (e.g. `clickup_get_filtered_team_tasks`, `clickup_get_bulk_tasks_time_in_status`)
  over many single-item calls.
- **`403` on Guests / Users / Audit logs / ACL.** These endpoints are **Enterprise-plan
  only** and return `403` on other plans. Affected tools include the `clickup_*_guest_*`
  and `clickup_*_user_*` families, `clickup_query_audit_logs`, and
  `clickup_update_privacy_and_access`. There is no workaround short of an Enterprise plan.
- **`403` when adding/removing tasks across Lists.** `clickup_add_task_to_list` and
  `clickup_remove_task_from_list` require the **Tasks in Multiple Lists** ClickApp to be
  enabled for the Workspace (Settings → ClickApps). Without it the API returns `403`.
- **v3 cursor pagination.** Chat, Docs search, and similar v3 list tools page with an
  opaque **cursor**, not `offset`. Read `next_cursor` from a response and pass it back as
  the `cursor` argument to fetch the next page; repeat until no cursor is returned.
- **`uvx clickup-mcp` runs the wrong thing.** The bare `clickup-mcp` PyPI name is an
  unrelated package. This server is published as **`amazing-clickup-mcp`** — always
  `uvx amazing-clickup-mcp`.
- **`401 Unauthorized` at first call.** The token is missing, malformed, or lacks access.
  Confirm `CLICKUP_API_TOKEN` is set in the client's `env` (or `.env`) and that it is a
  valid personal (`pk_…`) or OAuth2 token. Run `clickup_health_check` to verify.

## Contributing

Conventional Commits drive releases (release-please). Before pushing, run the full gate —
it must exit `0`:

```bash
uv run pytest -m "not live" && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/
```

## License

[Apache-2.0](LICENSE)
