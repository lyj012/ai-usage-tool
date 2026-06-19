#!/usr/bin/env python3
"""Minimal HTTP MCP server for ChatGPT Remote MCP access.

This module intentionally keeps HTTP transport separate from the existing
stdio MCP server so local Codex MCP usage remains unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mcp_server import SERVER_NAME, SERVER_VERSION, TOOLS, handle_request


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOKEN_ENV_NAME = "AIUSAGE_MCP_TOKEN"


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def is_local_host(host: str) -> bool:
    normalized = host.split("%", 1)[0]
    return normalized in {"127.0.0.1", "::1", "localhost"}


def parse_bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def is_authorized(client_host: str, authorization_header: str | None, expected_token: str | None) -> bool:
    supplied_token = parse_bearer_token(authorization_header)
    if supplied_token is None and is_local_host(client_host):
        return True
    if not expected_token:
        return False
    return supplied_token == expected_token


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}-http/{SERVER_VERSION}"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "transport": "http",
                "status": "ok",
                "tools": len(TOOLS),
            },
        )

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        token = os.environ.get(TOKEN_ENV_NAME)
        client_host = self.client_address[0] if self.client_address else ""
        if not is_authorized(client_host, self.headers.get("Authorization"), token):
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        try:
            body = self.rfile.read(length).decode("utf-8")
            request = json.loads(body or "{}")
        except Exception as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json", "message": str(exc)})
            return
        if not isinstance(request, dict):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": "Request body must be a JSON object"})
            return

        response = handle_request(request)
        if response is None:
            self.send_json(HTTPStatus.ACCEPTED, {"status": "accepted"})
            return
        self.send_json(HTTPStatus.OK, response)

    def send_json(self, status: HTTPStatus, payload: Any) -> None:
        raw = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), McpHttpHandler)


def serve_http(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    server = build_server(host, port)
    print(f"{SERVER_NAME} HTTP MCP listening on http://{host}:{port}/mcp", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Usage Tool HTTP MCP server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host, default {DEFAULT_HOST}.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port, default {DEFAULT_PORT}.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(serve_http(args.host, args.port))
