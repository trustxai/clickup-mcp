"""Allow running the server with `python -m clickup_mcp`."""

from __future__ import annotations

from clickup_mcp.server import main_stdio

if __name__ == "__main__":
    main_stdio()
