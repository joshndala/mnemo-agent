"""stdio MCP transport for mnemo.

Reads newline-delimited JSON-RPC 2.0 from stdin, writes responses to stdout.
Used by Claude Desktop, Cursor, and other MCP clients that spawn a subprocess.

Usage (via CLI):
  mnemo serve --agent job-prep --stdio

Claude Desktop config (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "mnemo-job-prep": {
        "command": "mnemo",
        "args": ["serve", "--agent", "job-prep", "--stdio"]
      }
    }
  }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mnemo.server import TOOL_REGISTRY, WRITE_TOOLS, _handle_jsonrpc


def run_stdio(agent: str, base: Path, read_only: bool = False) -> None:
    """Run the MCP server in stdio mode (newline-delimited JSON-RPC 2.0)."""

    def active_tools() -> list[dict]:
        if read_only:
            return [t for t in TOOL_REGISTRY if t["name"] not in WRITE_TOOLS]
        return TOOL_REGISTRY

    # Flush stdout immediately so clients don't block waiting for newlines
    out = sys.stdout

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            body = json.loads(raw_line)
        except json.JSONDecodeError as e:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            out.write(json.dumps(response) + "\n")
            out.flush()
            continue

        response = _handle_jsonrpc(body, agent, base, read_only, active_tools)

        # Notifications produce an empty dict — don't write a response
        if not response:
            continue

        out.write(json.dumps(response) + "\n")
        out.flush()
