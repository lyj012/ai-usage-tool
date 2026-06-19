# Changelog

## Unreleased

- Correct MCP `outputSchema` so each tool describes its `structuredContent`, and add schema validation tests.
- Make Remote HTTP `tools/list` hide local-only `config` and raw `session_id` inputs.
- Add stable Remote `session_ref` support for cross-tool session detail lookup.
- Add HTTP JSON-RPC boundary checks for `Accept`, `Origin`, `MCP-Protocol-Version`, `GET /mcp`, notifications, and batch rejection.
- Improve Remote text redaction for URLs, common token/key/password patterns, UNC paths, and paths containing spaces.
- Fix editable install metadata by explicitly declaring top-level Python modules and separating package version `0.3.0` from MCP server version `3.0.0`.
- Harden Remote HTTP MCP authentication: when `AIUSAGE_MCP_TOKEN` is set, localhost-forwarded requests must also send the correct bearer token.
- Add a sanitized Remote MCP response boundary that removes local paths, source files, full AI input text, emails, full hashes, and raw session IDs from HTTP responses.
- Prevent Remote MCP callers from overriding the server-side config path.
- Add HTTP request size, content length, method, path, and cache-control safeguards.
- Add stricter MCP argument validation and tool schema metadata for read-only behavior.
- Add security, environment, config, and packaging metadata files.

## 2026-06-19

- Added local stdio MCP server for reading personal work reports.
- Added first HTTP MCP transport prototype for tunnel-based experiments.
- Removed legacy v1 export flow and kept the project focused on personal development work analysis.
