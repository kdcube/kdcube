---
id: ks:docs/sdk/tools/mcp-README.md
title: "MCP"
summary: "MCP tool integration: descriptor allow-lists, MCP_SERVICES transport/auth config, and runtime execution flow (host + isolated)."
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
2. `MCP_SERVICES` env JSON (how to connect and authenticate).

### 1) Descriptor: `MCP_TOOL_SPECS`

```python
MCP_TOOL_SPECS = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
]
```

Rules:
- `server_id` must match an entry in `MCP_SERVICES`.
- `alias` is used in tool IDs: `mcp.<alias>.<tool_id>`.
- `tools` omitted or `["*"]` exposes all server tools.
- A concrete list is an allow-list.

### 2) Environment: `MCP_SERVICES`

Supported top-level keys:
- `mcpServers` (preferred)
- `servers` (also supported)

```bash
export MCP_SERVICES='{
  "mcpServers": {
    "stack": {
      "transport": "stdio",
      "command": "npx",
      "args": ["mcp-remote", "mcp.stackoverflow.com"]
    },
    "docs": {
      "transport": "http",
      "url": "https://mcp.example.com",
      "auth": { "type": "bearer", "env": "MCP_DOCS_TOKEN" }
    },
    "local": {
      "transport": "sse",
      "url": "http://127.0.0.1:8787/sse"
    }
  }
}'
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
- `bearer` / `api_key` / `header` auth reads secrets from env and injects headers.
- Secrets are not written to Redis cache.

## Runtime execution flow

1. `MCPToolsSubsystem.build_tool_entries()` contributes MCP entries to the tool catalog.
2. Planner selects an MCP tool ID: `mcp.<alias>.<tool_id>`.
3. `io_tools.tool_call(...)` parses `mcp` origin and routes to `MCPToolsSubsystem.execute_tool(...)`.
4. In isolated runtime, executor still calls `io_tools.tool_call(...)`; tool calls are delegated to supervisor, and MCP routing still happens there.

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

```bash
export MCP_SERVICES='{
  "mcpServers": {
    "web_search": {
      "transport": "stdio",
      "command": "python",
      "args": [
        "-m",
        "kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server",
        "--transport",
        "stdio"
      ]
    }
  }
}'
```

## Troubleshooting

- No MCP tools in catalog:
  - check `MCP_TOOL_SPECS` has the server alias entry.
  - check `MCP_SERVICES` has a matching `server_id`.
  - validate transport fields (`command` for stdio, `url` for http/sse).
  - verify auth is not interactive (`oauth_gui`).
- MCP call fails at runtime:
  - confirm final tool ID is `mcp.<alias>.<tool_id>`.
  - confirm server exposes that `tool_id` and allow-list includes it.
