#!/usr/bin/env python3
"""Minimal HTTP MCP server for ChatGPT Remote MCP access.

This module intentionally keeps HTTP transport separate from the existing
stdio MCP server so local Codex MCP usage remains unchanged.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from mcp_server import PROTOCOL_VERSION, SERVER_NAME, handle_request


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOKEN_ENV_NAME = "AIUSAGE_MCP_TOKEN"
CONFIG_ENV_NAME = "AIUSAGE_MCP_CONFIG"
ALLOWED_ORIGINS_ENV_NAME = "AIUSAGE_MCP_ALLOWED_ORIGINS"
MAX_BODY_BYTES = 64 * 1024


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
    if expected_token:
        return supplied_token is not None and hmac.compare_digest(supplied_token, expected_token)
    if supplied_token is None and is_local_host(client_host):
        return True
    return False


def accepts_json(header_value: str | None) -> bool:
    if not header_value:
        return True
    values = [part.split(";", 1)[0].strip().lower() for part in header_value.split(",")]
    return "*/*" in values or "application/*" in values or "application/json" in values


def is_allowed_origin(origin: str | None, allowed_origins_text: str | None) -> bool:
    if not origin:
        return True
    allowed = {item.strip() for item in (allowed_origins_text or "").split(",") if item.strip()}
    return origin in allowed


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def version_string(self) -> str:
        return SERVER_NAME

    def do_GET(self) -> None:
        path = self.path_only()
        if path == "/mcp":
            self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "sse_not_supported", "message": "GET /mcp is not supported by this experimental JSON-RPC transport."})
            return
        if path != "/health":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "status": "ok",
            },
        )

    def do_POST(self) -> None:
        if self.path_only() != "/mcp":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not accepts_json(self.headers.get("Accept")):
            self.send_json(HTTPStatus.NOT_ACCEPTABLE, {"error": "not_acceptable"})
            return
        if not is_allowed_origin(self.headers.get("Origin"), os.environ.get(ALLOWED_ORIGINS_ENV_NAME)):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "origin_not_allowed"})
            return
        protocol_version = self.headers.get("MCP-Protocol-Version")
        if protocol_version and protocol_version != PROTOCOL_VERSION:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "unsupported_protocol_version"})
            return
        token = os.environ.get(TOKEN_ENV_NAME)
        client_host = self.client_address[0] if self.client_address else ""
        if not is_authorized(client_host, self.headers.get("Authorization"), token):
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        try:
            content_length = self.headers.get("Content-Length")
            if content_length is None:
                self.send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
                return
            length = int(content_length)
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if length < 0:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if length > MAX_BODY_BYTES:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return
        try:
            body = self.rfile.read(length).decode("utf-8")
            request = json.loads(body or "{}")
        except UnicodeDecodeError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_utf8"})
            return
        except json.JSONDecodeError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(request, dict):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": "Request body must be a JSON object"})
            return

        response = handle_request(request, remote=True, remote_config=getattr(self.server, "remote_config", "aiusage-config.json"))
        if response is None:
            self.send_empty(HTTPStatus.ACCEPTED)
            return
        self.send_json(HTTPStatus.OK, response)

    def do_PUT(self) -> None:
        self.method_not_allowed()

    def do_PATCH(self) -> None:
        self.method_not_allowed()

    def do_DELETE(self) -> None:
        self.method_not_allowed()

    def method_not_allowed(self) -> None:
        self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method_not_allowed"})

    def path_only(self) -> str:
        return urlsplit(self.path).path

    def send_json(self, status: HTTPStatus, payload: Any) -> None:
        raw = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        self.end_headers()
        self.wfile.write(raw)

    def send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        self.end_headers()


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), McpHttpHandler)
    server.remote_config = os.environ.get(CONFIG_ENV_NAME) or "aiusage-config.json"  # type: ignore[attr-defined]
    return server


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
