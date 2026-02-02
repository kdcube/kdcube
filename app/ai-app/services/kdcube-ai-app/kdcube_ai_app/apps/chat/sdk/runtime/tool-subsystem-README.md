# Tool Subsystem

This document explains how tools are defined, loaded, and executed by the SDK. It covers custom tool modules, tool IDs, and runtime selection (in‑process vs isolation).

## What the ToolSubsystem does

ToolSubsystem is the single place that:
- Resolves tool module specs (`module` or `ref`) into importable modules.
- Loads tool modules and introspects tool metadata (SK/non‑SK).
- Builds tool catalogs for the coordinator/decision prompts.
- Provides callables for `<alias>.<tool_name>` at runtime.
- Exports runtime globals for isolated execution.
- Provides optional per‑tool runtime overrides.
- Wires MCP tools (via MCPToolsSubsystem) into the tool catalog.

Implementation: `kdcube_ai_app/apps/chat/sdk/runtime/tool_subsystem.py`

## Defining tools (bundle descriptor)

Bundles provide tool modules via a portable descriptor, e.g.:

`bundle_root/tools_descriptor.py`

```python
CODEGEN_TOOLS_SPECS = [
    {"module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools", "alias": "io_tools", "use_sk": True},
    {"ref": "orchestrator/tools/generic_agent_tools.py", "alias": "generic_tools", "use_sk": True},
]
```

### Tool IDs

Tool IDs are resolved as:

```
<alias>.<tool_name>
```

Example: alias `generic_tools` + function `web_search` → `generic_tools.web_search`.

For non‑module tools (e.g., MCP), tool IDs include a provider origin prefix:

```
<origin>.<provider>.<tool_name...>
```

Example: `mcp.web_search.web_search`

## Runtime selection per tool

By default, built‑in tools use SDK policy (`tools_insights`). For custom tools, you can override runtime by defining a mapping in your bundle:

```python
TOOL_RUNTIME = {
    "generic_tools.web_search": "local",
    "generic_tools.fetch_url_contents": "local",
}
```

Valid values: `none | local | local_network | docker`.

If a tool is **not** present in the mapping, it runs in‑memory (unless the SDK default policy isolates it).

## How runtime selection flows

1) The bundle passes `TOOL_RUNTIME` into `SolverSystem` (see workflow wiring).
2) `ToolSubsystem` stores the runtime map and exposes `get_tool_runtime(tool_id)`.
3) `react/execution.py` checks for a runtime override before falling back to SDK defaults.

This keeps built‑in tools behavior stable while allowing bundles to opt into isolation.

## Tool loading path

- `ToolSubsystem` resolves specs via `resolve_codegen_tools_specs(...)`.
- Modules are imported and bound with:
  - `bind_service(...)` (ModelService)
  - `bind_registry(...)` (bundle registry)
  - `bind_integrations(...)` (ctx client, kv cache)
- Tool metadata is introspected and a flattened catalog is built.

## Integrations available to tools

Tools can access shared integrations injected by the subsystem:
- `ctx_client`: ContextRAGClient / ContextBrowser helper to query or persist artifacts.
- `kv_cache`: Redis-backed KV cache for lightweight state (see `infra/service_hub/cache-README.md`).

Prefer cache access over direct Redis calls inside tools.

## MCP tools

If MCP tools are configured, `ToolSubsystem` delegates MCP discovery/execution
to `MCPToolsSubsystem` and injects MCP tool entries into the catalog.

See: `sdk/runtime/mcp/mcp-README.md`

## Connection to runtime

- In‑memory tools are executed directly via `ToolSubsystem.resolve_callable`.
- Isolated tools are executed via ISO runtime, using:
  - `export_runtime_globals()` (alias maps + tool module files)
  - `tool_modules_tuple_list()` (module objects for in‑proc runtime)

See:
- `sdk/runtime/isolated/README-iso-runtime.md`
- `sdk/runtime/isolated/README-runtime-modes-builtin-tools.md`
