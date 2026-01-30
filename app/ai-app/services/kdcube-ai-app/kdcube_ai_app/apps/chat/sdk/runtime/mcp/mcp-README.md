# MCP Integration (Runtime)

This document describes how MCP (Model Context Protocol) tools are wired into the SDK runtime, how to configure servers, and what is supported.

## Architecture overview

MCP is integrated as a first‑class tool provider alongside module tools.

Flow:
1. **Tool descriptor** lists which MCP servers/tools are exposed to planners.
2. **MCP_SERVICES env** provides server connection details + secrets.
3. **MCPToolsSubsystem** loads tool schemas (cached) and exposes them as tool entries:
   - tool id format: `mcp.<alias>.<tool_id>`
4. **Execution** routes MCP tool calls through `io_tools.tool_call(...)` and
   delegates to MCPToolsSubsystem.

## Configuration

### 1) Tool descriptor (which MCP servers/tools are exposed)

In your bundle tool descriptor, declare MCP servers and optional allow‑lists.

Example:
```
MCP_TOOL_SPECS = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
    {"server_id": "local", "alias": "local", "tools": ["*"]},
]
```

Notes:
- `server_id` must match a server entry in `MCP_SERVICES`.
- `alias` controls the tool id namespace: `mcp.<alias>.<tool_id>`.
- `tools` can be omitted to expose all tools. Use a list to allow‑list specific tool ids.

### 2) Environment config (how to connect + secrets)

All connection details live in **one env var**: `MCP_SERVICES`.

Supported top‑level keys:
- `mcpServers` (preferred, matches common MCP config)
- `servers` (also supported)

Example (stdio + http + sse):
```
export MCP_SERVICES='{
  "mcpServers": {
    "stack": {
      "transport": "stdio",
      "command": "npx",
      "args": ["mcp-remote", "mcp.stackoverflow.com"],
      "auth": { "type": "oauth_gui" }
    },
    "docs": {
      "transport": "http",
      "url": "https://mcp.example.com",
      "auth": { "type": "bearer", "env": "MCP_DOCS_TOKEN" }
    },
    "local": {
      "transport": "sse",
      "url": "http://127.0.0.1:8787/sse",
      "auth": { "type": "none" }
    }
  }
}'
```

Auth handling:
- `oauth_gui` / interactive auth → **server is hidden** (tools are not listed)
- `bearer` / `api_key` / `header` → token is read from env and injected into headers
 - secrets never go to Redis cache; they stay in env

## Supported transports

Default transport is **stdio**.

| transport        | Required fields              | Notes |
|------------------|------------------------------|------|
| `stdio`          | `command` (and optional args) | Used for local process or `npx mcp-remote …` |
| `http`           | `url`                         | Uses Streamable HTTP (JSON‑RPC) |
| `streamable-http`| `url`                         | Same as `http` |
| `sse`            | `url`                         | Server‑sent events transport |

## Execution model

1. ReAct/Decision selects tool id: `mcp.<alias>.<tool_id>`.
2. `io_tools.tool_call` routes to MCPToolsSubsystem for MCP origin.
3. MCPToolsSubsystem invokes the MCP client adapter (Python SDK).

## What is NOT supported

- Interactive OAuth flows (GUI/consent). These servers are **hidden**.
- Storing secrets in Redis cache (secrets stay in env).
- Dotted aliases (provider alias is a single segment).

## Examples by transport

**stdio (local binary)**
```
export MCP_SERVICES='{
  "mcpServers": {
    "local_bin": {
      "transport": "stdio",
      "command": "/usr/local/bin/my-mcp",
      "args": ["--verbose"]
    }
  }
}'
```

**stdio (remote via mcp-remote)**
```
export MCP_SERVICES='{
  "mcpServers": {
    "stack": {
      "transport": "stdio",
      "command": "npx",
      "args": ["mcp-remote", "mcp.stackoverflow.com"]
    }
  }
}'
```

**HTTP (bearer token)**
```
export MCP_DOCS_TOKEN="..."
export MCP_SERVICES='{
  "mcpServers": {
    "docs": {
      "transport": "http",
      "url": "https://mcp.example.com",
      "auth": { "type": "bearer", "env": "MCP_DOCS_TOKEN" }
    }
  }
}'
```

**SSE (no auth)**
```
export MCP_SERVICES='{
  "mcpServers": {
    "local": {
      "transport": "sse",
      "url": "http://127.0.0.1:8787/sse"
    }
  }
}'
```

## Local MCP server: web_search

This repo provides a built‑in MCP server wrapper for web search:
`kdcube_ai_app/apps/chat/sdk/tools/mcp/web_search_server.py`

It can run:
- **stdio** (on‑demand)
- **sse** (HTTP streaming)
- **http** (streamable HTTP)

### Run locally (stdio)
```
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search_server --transport stdio
```

### Run as SSE server
```
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search_server --transport sse --host 0.0.0.0 --port 8787
```

### Run as HTTP server
```
python -m kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search_server --transport http --host 0.0.0.0 --port 8787
```

### Client config for stdio
```
export MCP_SERVICES='{
  "mcpServers": {
    "web_search": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search_server", "--transport", "stdio"]
    }
  }
}'
```

### Client config for SSE
```
export MCP_SERVICES='{
  "mcpServers": {
    "web_search": {
      "transport": "sse",
      "url": "http://127.0.0.1:8787/sse"
    }
  }
}'
```

### Env vars used by the server
```
# model service
OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY
DEFAULT_LLM_MODEL_ID (optional)
ROLE_MODELS_JSON (optional)

# cache (optional)
REDIS_URL
TENANT_ID
DEFAULT_PROJECT_NAME
WEB_SEARCH_CACHE_TTL_SECONDS (optional)
```

## Quick troubleshooting

- If no MCP tools show up, check:
  - `MCP_TOOL_SPECS` includes your server id
  - `MCP_SERVICES` has a matching server entry
  - transport config is valid (stdio needs `command`, http/sse needs `url`)
  - auth is not `oauth_gui`
