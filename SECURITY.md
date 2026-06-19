# Security Policy

AI Usage Tool reads local AI session records, Git metadata, reflections, and generated reports. Treat `data/`, `aiusage-config.json`, `.env`, tunnel URLs, and MCP tokens as private.

## Supported Scope

This repository currently supports local personal use. Remote HTTP MCP is experimental and should only be exposed through a trusted HTTPS tunnel with a strong bearer token.

## Reporting Security Issues

Please report security issues privately by opening a GitHub issue with minimal reproduction details and no private report data, tokens, local paths, or AI prompts. If the issue contains sensitive material, first ask for a private contact channel in the issue.

## Sensitive Files

Do not commit:

- `aiusage-config.json`
- `.env`
- `data/`
- local AI session exports
- tunnel tokens or URLs
- logs, caches, virtual environments, or Streamlit local config

## Remote MCP Notes

- Set `AIUSAGE_MCP_TOKEN` before exposing the server through Cloudflare Tunnel, ngrok, or similar tools.
- When a token is configured, every `/mcp` request must include `Authorization: Bearer <token>`, including requests forwarded from localhost.
- Remote HTTP MCP returns a sanitized view and ignores caller-supplied `config` paths.
- The HTTP transport is not yet verified with ChatGPT connector or MCP Inspector in this repository.
