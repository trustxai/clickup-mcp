"""Tool registration for clickup_mcp.

ALL wave modules are listed here from day one (even while some are still empty
stubs) so that parallel worktrees implementing individual modules never need to
touch this file — eliminating merge conflicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_all(mcp: FastMCP) -> None:
    """Import every tool module; @mcp.tool decorators self-register on import."""
    from clickup_mcp.tools import (  # noqa: F401
        attachments,
        chat_channels,
        chat_messages,
        checklists,
        comments,
        custom_fields,
        docs,
        folders,
        goals,
        guests,
        health,
        lists,
        members,
        relationships,
        spaces,
        tags,
        tasks,
        time_tracking,
        users,
        views,
        webhooks,
        workspace,
    )
