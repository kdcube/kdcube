---
id: ks:docs/sdk/tools/mcp-README.md
title: "MCP"
summary: "MCP tool integration: descriptor allow-lists, bundle-props MCP service config, named-secret auth, and runtime execution flow (host + isolated)."
tags: ["sdk", "tools", "mcp", "runtime", "descriptor", "transport", "auth"]
keywords: ["MCP_TOOL_SPECS", "MCP_SERVICES", "MCPToolsSubsystem", "mcp.<alias>.<tool>", "stdio", "http", "streamable-http", "sse", "oauth_gui", "tool_call"]
see_also:
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
  - ks:docs/exec/README-iso-runtime.md
---
# MCP Integration

This document covers MCP (Model Context Protocol) as a tool provider in the SDK.

For shared tool-subsystem behavior (`TOOLS_SPECS`, alias resolution, isolated supervisor flow), see [Tool Subsystem](./tool-subsystem-README.md).

## What you configure

You configure MCP in two places:
1. `MCP_TOOL_SPECS` in bundle `tools_descriptor.py` (what is visible/exposed).
2. Bundle props `mcp.services` (how to connect and authenticate).

`MCP_SERVICES` env JSON is still supported as a legacy / local-dev fallback, but
it is not the preferred platform contract.

### 1) Descriptor: `MCP_TOOL_SPECS`

```python
MCP_TOOL_SPECS = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
]
```

Rules:
- `server_id` must match an entry in bundle props `mcp.services` (or legacy `MCP_SERVICES` fallback).
- `alias` is used in tool IDs: `mcp.<alias>.<tool_id>`.
- `tools` omitted or `["*"]` exposes all server tools.
- A concrete list is an allow-list.

### 2) Bundle props: `mcp.services`

Supported top-level keys:
- `mcpServers` (preferred)
- `servers` (also supported)

```yaml
mcp:
  services:
    mcpServers:
      stack:
        transport: stdio
        command: npx
        args: ["mcp-remote", "mcp.stackoverflow.com"]
      docs:
        transport: http
        url: https://mcp.example.com
        auth:
          type: bearer
          secret: bundles.react.mcp@2026-03-09.secrets.docs.token
      local:
        transport: sse
        url: http://127.0.0.1:8787/sse
```

Legacy/dev fallback:

```bash
export MCP_SERVICES='{"mcpServers":{"docs":{"transport":"http","url":"https://mcp.example.com"}}}'
```

## Supported transports

| transport         | Required fields                | Notes |
|------------------|--------------------------------|------|
| `stdio`          | `command` (+ optional `args`)  | Local process or `npx mcp-remote ...` |
| `http`           | `url`                          | Streamable HTTP JSON-RPC |
| `streamable-http`| `url`                          | Alias of `http` |
| `sse`            | `url`                          | Server-sent events |

## Auth behavior

- `oauth_gui` / interactive auth servers are hidden (not listed in tool catalog).
- `bearer` / `api_key` / `header` auth supports either:
  - `auth.secret` → `get_secret("dot.path.key")`
  - `auth.env` → env lookup / `get_secret(env_key)` fallback
- Secrets are not written to Redis cache.

## Secret resolution: named secrets and `${secret:...}` syntax

Two secret patterns are supported:

1. Auth block secret resolution:

```json
"auth": {
  "type": "bearer",
  "secret": "bundles.react.mcp@2026-03-09.secrets.docs.token"
}
```

2. Stdio env interpolation:

For stdio servers, env values can use the `${secret:dot.path.key}` syntax to
resolve secrets via `get_secret()` at session creation time:

```json
"env": {
  "FIRECRAWL_API_KEY": "${secret:services.firecrawl.api_key}"
}
```

`get_secret()` resolution order:
1. Environment variables (via `_SECRET_ALIASES` in `sdk/config.py`)
2. Settings attributes (Pydantic BaseSettings)
3. Secrets manager provider (secrets-service / AWS SM / in-memory)

**Local dev:** secrets come from env vars in `.env.proc`. Each dot-path key
needs a corresponding alias in `_SECRET_ALIASES` (e.g.,
`"services.firecrawl.api_key": ["FIRECRAWL_API_KEY"]`).

**CLI deploy:** the CLI reads `secrets.yaml` / `bundles.secrets.yaml` and
injects values into the secrets-service sidecar or AWS Secrets Manager.

Bundle-props `mcp.services` is preferred because it is bundle-scoped and can
use named bundle secrets. `MCP_SERVICES` remains useful for process-wide local
dev experiments only.

## Runtime execution flow

1. `MCPToolsSubsystem.build_tool_entries()` contributes MCP entries to the tool catalog.
2. Planner selects an MCP tool ID: `mcp.<alias>.<tool_id>`.
3. `io_tools.tool_call(...)` parses `mcp` origin and routes to `MCPToolsSubsystem.execute_tool(...)`.
4. In isolated runtime, executor still calls `io_tools.tool_call(...)`; tool calls are delegated to supervisor, and MCP routing still happens there.
5. For isolated exec, the current MCP service config is exported from the live tool subsystem into runtime globals; it is not expected to be rebuilt from a fresh process env.

## Tool ID format

- Format: `mcp.<alias>.<tool_id>`
- Alias must be a single segment (no dots).

## Local MCP server example: `web_search`

Server module:
- [`kdcube_ai_app/apps/chat/sdk/tools/mcp/web_search/web_search_server.py`](../../../services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tools/mcp/web_search/web_search_server.py)

Run server (stdio):

```bash
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server --transport stdio
```

Run server (sse):

```bash
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server --transport sse --host 0.0.0.0 --port 8787
```

Run server (http):

```bash
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server --transport http --host 0.0.0.0 --port 8787
```

Client config example:

```yaml
mcp:
  services:
    mcpServers:
      web_search:
        transport: stdio
        command: python
        args:
          - -m
          - kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server
          - --transport
          - stdio
```

## Troubleshooting

- No MCP tools in catalog:
  - check `MCP_TOOL_SPECS` has the server alias entry.
  - check bundle props `mcp.services` has a matching `server_id` (or legacy `MCP_SERVICES` fallback).
  - validate transport fields (`command` for stdio, `url` for http/sse).
  - verify auth is not interactive (`oauth_gui`).
- MCP call fails at runtime:
  - confirm final tool ID is `mcp.<alias>.<tool_id>`.
  - confirm server exposes that `tool_id` and allow-list includes it.
