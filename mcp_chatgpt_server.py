#!/usr/bin/env python3
"""ChatGPT-facing Streamable HTTP MCP server.

This entrypoint uses the official MCP Python SDK transport instead of the
project's legacy hand-written HTTP JSON-RPC shim. It keeps all tool execution
behind mcp_server.handle_request(remote=True), so ChatGPT receives the same
server-controlled config and sanitized data view as the Remote-safe path.
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp_server import SERVER_NAME, handle_request


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CONFIG = "aiusage-config.json"


def call_remote_tool(name: str, arguments: dict[str, Any], config_path: str) -> dict[str, Any]:
    request = {
        "jsonrpc": "2.0",
        "id": name,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = handle_request(request, remote=True, remote_config=config_path)
    if response is None:
        return {"is_error": True, "error": {"message": "MCP notification produced no response."}}
    if "error" in response:
        return {"is_error": True, "error": response["error"]}
    result = response.get("result") or {}
    structured = result.get("structuredContent") or {}
    if result.get("isError"):
        return {**structured, "is_error": True}
    return structured


def build_mcp(config_path: str) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "Missing MCP Python SDK. Install ChatGPT MCP dependencies with: "
            'python -m pip install -e ".[chatgpt]"'
        ) from exc

    mcp = FastMCP(
        SERVER_NAME,
        stateless_http=True,
        json_response=True,
        instructions=(
            "Read-only local personal development work reports. "
            "Use these tools only to inspect existing local daily reports and trends."
        ),
    )

    @mcp.tool(name="get_daily_work_report")
    def get_daily_work_report(date: str) -> dict[str, Any]:
        """Read a local daily development work report by date in YYYY-MM-DD format."""
        return call_remote_tool("get_daily_work_report", {"date": date}, config_path)

    @mcp.tool(name="get_work_trend")
    def get_work_trend(from_date: str, to_date: str, person: str = "") -> dict[str, Any]:
        """Read aggregated work trend data for a date range."""
        arguments = {"from": from_date, "to": to_date}
        if person:
            arguments["person"] = person
        return call_remote_tool("get_work_trend", arguments, config_path)

    @mcp.tool(name="search_work_records")
    def search_work_records(query: str, from_date: str = "", to_date: str = "", limit: int = 20) -> dict[str, Any]:
        """Search local daily reports for outcomes, topics, rework, commits, and associations."""
        arguments: dict[str, Any] = {"query": query, "limit": limit}
        if from_date or to_date:
            arguments["from"] = from_date
            arguments["to"] = to_date
        return call_remote_tool("search_work_records", arguments, config_path)

    @mcp.tool(name="get_git_activity")
    def get_git_activity(date: str) -> dict[str, Any]:
        """Read Git activity from a local daily report by date in YYYY-MM-DD format."""
        return call_remote_tool("get_git_activity", {"date": date}, config_path)

    @mcp.tool(name="get_ai_session_details")
    def get_ai_session_details(date: str, session_ref: str = "") -> dict[str, Any]:
        """Read sanitized AI session details by date and optional session_ref."""
        arguments = {"date": date}
        if session_ref:
            arguments["session_ref"] = session_ref
        return call_remote_tool("get_ai_session_details", arguments, config_path)

    return mcp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Usage Tool ChatGPT Streamable HTTP MCP server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host, default {DEFAULT_HOST}.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port, default {DEFAULT_PORT}.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"Server-controlled config path, default {DEFAULT_CONFIG}.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        mcp = build_mcp(args.config)
    except RuntimeError as exc:
        print(str(exc), flush=True)
        return 1
    mcp.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
