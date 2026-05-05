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
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/agents/claude/claude-code-README.md
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
          secret: b:docs.token
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

## Correct MCP server requirements

An MCP integration is considered correct when it is explicit about all runtime
boundaries: discovery, transport, auth, network reachability, lifecycle, tool
schemas, output size, and retry behavior.

Minimum checklist:

- the server is configured in bundle props `mcp.services` or in a generated MCP
  client config owned by the caller
- the visible tools are allow-listed through `MCP_TOOL_SPECS` or the client
  runtime's equivalent allow-list
- every tool name is stable and has a bounded input schema
- every tool returns bounded structured data; large files or artifacts should be
  returned as references, not huge inline payloads
- write tools are idempotent, run-scoped, or protected by caller-supplied ids so
  retries do not duplicate side effects
- auth is non-interactive in production and secrets are resolved through
  `get_secret(...)`, bundle secrets, or the runtime secret provider
- no secret values are written to bundle props, logs, tool responses, or
  generated workspace files unless the file is explicitly a short-lived local
  client config
- HTTP/SSE endpoints are reachable from the process that acts as MCP client
- stdio commands and their dependencies exist in the process that starts them
- streamable HTTP FastMCP apps are either created with `stateless_http=True` or
  are run behind a server/lifespan path that correctly initializes the MCP
  session manager
- logs include enough non-secret context to debug failures: server alias,
  transport, URL host, tool id, run id when applicable, and final status

For bundle-served MCP endpoints exposed with `@mcp(...)`, read:

- [Bundle Transports](../bundle/bundle-transports-README.md)
- [Bundle Agent Integration](../bundle/bundle-agent-integration-README.md)

For Claude Code consumers, remember that KDCube `mcp.services` does not
configure Claude Code. The bundle must write Claude-compatible MCP config into
the Claude workspace, usually `.mcp.json`, and the configured URL must be
reachable from the process/container that runs `claude`.
Use `ClaudeCodeWorkspaceConfig` / `prepare_claude_code_workspace(...)` from the
Claude Code SDK when you want the SDK to write the standard workspace files.

## MCP Tool Results And React File Hosting

When MCP tools are called through the React tool subsystem, their result is
handled by the same tool-result pipeline as bundle-local tools. A tool should
return the standard envelope when it is using the KDCube tool contract:

```json
{"ok": true, "error": null, "ret": {...}}
```

If an MCP-backed tool intentionally materializes files into the current React
`OUT_DIR`, it can opt into artifact hosting by putting a file declaration inside
`ret`:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "path": "turn_123/outputs/export.csv",
        "filename": "export.csv",
        "mime_type": "text/csv",
        "visibility": "external"
      }
    ]
  }
}
```

React v2 and v3 unwrap the envelope and host declared files only when
`ret.artifact_type == "files"`. The declared path must be accessible from
the React runtime, usually as an `OUT_DIR`-relative path under
`turn_<id>/outputs/...`. Remote MCP services that cannot write to that workspace
should return data or already-hosted references through an explicit product tool
contract instead of relying on automatic local hosting.

Bundle-local tools that call MCP internally may also host files themselves with
`bundle_tool_context.host_files(...)` after materializing the files. That helper
runs in the trusted bundle tool runtime, including isolated supervisor
execution. A pure remote MCP server does not receive the KDCube conversation
hosting service; it should either return the strict file declaration for files
that exist in the React workspace, or return product data that a bundle-local
tool can materialize and host.

For `host_files(...)` to work, the bundle-local tool must be running inside a
prepared KDCube tool context: active `ToolSubsystem`, hosting service,
communicator scope with tenant/project/user/conversation/turn, conversation
storage, and output directory. The normal React path prepares this in
`BaseWorkflow.build_react(...)`; isolated execution prepares it through
`bootstrap_bind_all(...)`. If the tool context is not prepared, the helper raises
a runtime error instead of producing an unscoped hosted file.

## HTTP reachability and localhost

For HTTP, `url` means "reachable from the MCP client process", not "reachable
from the developer's browser".

Deployment rules:

- same local host: `http://127.0.0.1:<port>` can work
- same container as the client process: `http://127.0.0.1:<port>` can work if
  the server listens there
- different Docker container: `127.0.0.1` points at the client container, so use
  Docker service DNS or another internal host name
- ECS same task with shared task networking: localhost can work if the target
  container listens on the expected port
- ECS separate task/service: use Cloud Map, internal ALB, or another private
  service endpoint

If the server is bundle-served through `@mcp(...)`, the route path is:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}
```

The caller still needs the correct scheme, host, and port for its deployment.

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
  "secret": "b:docs.token"
}
```

2. Stdio env interpolation:

For stdio servers, env values can use the `${secret:dot.path.key}` syntax to
resolve secrets via `get_secret()` at session creation time:

```json
"env": {
  "FIRECRAWL_API_KEY": "${secret:b:firecrawl.api_key}"
}
```

For bundle-local MCP configs, prefer:
- `b:...` for current bundle secrets
- no prefix / `a:...` for platform/global secrets

Fully qualified canonical keys such as `bundles.<bundle_id>.secrets...` are
still accepted when the explicit form is needed.

`get_secret()` resolution order:
1. Environment variables (via `_SECRET_ALIASES` in `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py`)
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

Dependency note:
- MCP server dependencies live with the MCP server itself, not with the bundle
  tool module that calls it
- that is different from bundle-local Python tools, where direct imports still
  depend on packages being present in the executing KDCube runtime unless the
  tool delegates into a bundle `@venv(...)` helper

## Tool ID format

- Format: `mcp.<alias>.<tool_id>`
- Alias must be a single segment (no dots).

## Local MCP server example: `web_search`

Server module:
- [`kdcube_ai_app/apps/chat/sdk/tools/mcp/web_search/web_search_server.py`](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tools/mcp/web_search/web_search_server.py)

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
  - confirm the MCP URL is reachable from the client process, not just from the
    host browser or another container.
  - for bundle-served FastMCP over streamable HTTP, confirm the app is
    `stateless_http=True` or has a valid lifespan/session-manager path.
  - confirm auth headers are present and that secrets resolve in the runtime
    that creates the MCP client.
