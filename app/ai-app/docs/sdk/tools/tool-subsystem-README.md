---
id: ks:docs/sdk/tools/tool-subsystem-README.md
title: "Tool Subsystem"
summary: "Canonical runtime flow for tool descriptors: resolution, dynamic loading, and execution in in-memory and isolated modes."
tags: ["sdk", "tools", "subsystem", "runtime", "descriptor", "isolation", "mcp"]
keywords: ["tools_descriptor.py", "TOOLS_SPECS", "MCP_TOOL_SPECS", "TOOL_RUNTIME", "ToolSubsystem", "resolve_codegen_tools_specs", "io_tools.tool_call", "ToolStub", "py_code_exec_entry.py", "rewrite_runtime_globals_for_bundle"]
see_also:
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/tools/mcp-README.md
  - ks:docs/exec/README-runtime-modes-builtin-tools.md
  - ks:docs/exec/README-iso-runtime.md
---
# Tool Subsystem

This is the canonical reference for how tool descriptors are consumed and how tool calls execute.

## Descriptor wiring

`tools_descriptor.py` is imported by the bundle workflow and passed to `create_tool_subsystem_with_mcp(...)` as data:

```python
tool_subsystem, _ = create_tool_subsystem_with_mcp(
    service=self.model_service,
    comm=self.comm,
    logger=self.logger,
    bundle_spec=self.config.ai_bundle_spec,
    context_rag_client=self.ctx_client,
    registry={"kb_client": self.kb},
    raw_tool_specs=tools_descriptor.TOOLS_SPECS,
    tool_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
    mcp_tool_specs=getattr(tools_descriptor, "MCP_TOOL_SPECS", []),
    mcp_env_json=os.environ.get("MCP_SERVICES") or "",
)
```

The subsystem does not auto-scan `tools_descriptor.py` on disk. The workflow decides what is loaded.

## `module` vs `ref` resolution

`TOOLS_SPECS` entries are portable:
- `module`: importable Python module path.
- `ref`: file path relative to the bundle root.

Resolution flow:
1. `resolve_codegen_tools_specs(...)` rewrites relative `ref` paths using the bundle root.
2. `ToolSubsystem._resolve_tools(...)` turns `module` and `ref` entries into concrete file paths.
3. `ToolSubsystem._load_tools_module(...)` loads each file with `importlib.util.spec_from_file_location(...)`.

Implication: `ref` is not host-only. It is bundle-relative and portable across runtimes.

## Why `ref` works in iso-runtime and Docker

`ToolSubsystem.export_runtime_globals()` exports:
- `TOOL_ALIAS_MAP`
- `TOOL_MODULE_FILES`
- `RAW_TOOL_SPECS`
- `BUNDLE_ROOT_HOST`

Before remote/isolated execution:
- `rewrite_runtime_globals_for_bundle(...)` rewrites bundle-root paths to the restored bundle path.
- `py_code_exec_entry.py` (`_bootstrap_supervisor_runtime`) loads dynamic modules from `TOOL_MODULE_FILES`.
- If a module file path is unavailable, the supervisor can still resolve `module` entries using `RAW_TOOL_SPECS`.

This is why bundle-local `ref` tools continue to work in isolated runtime execution.

## Tool IDs and catalog entries

Tool IDs:
- Module tools: `<alias>.<tool_name>`
- MCP tools: `mcp.<alias>.<tool_name>`

`ToolSubsystem` introspects loaded modules and builds the catalog used by planner/generator prompts.

## Execution path (runtime enforcement)

1. `execution.execute_tool(...)` picks in-memory vs isolated execution (`TOOL_RUNTIME` + default isolation policy).
2. In-memory path (`_execute_tool_in_memory`) resolves callable by alias and executes via `agent_io_tools.tool_call(...)`.
3. Isolated path (`execute_tool_in_isolation`) passes runtime globals to iso runtime.
4. In the limited executor, `agent_io_tools.tool_call(...)` delegates to supervisor via `ToolStub`.
5. Supervisor resolves callable from `TOOL_ALIAS_MAP` and executes through `agent_io_tools.tool_call(...)`.
6. For `mcp.*` IDs, `agent_io_tools.tool_call(...)` routes to `MCPToolsSubsystem.execute_tool(...)`.

## Related docs

- [Custom Tools](./custom-tools-README.md)
- [MCP Integration](./mcp-README.md)
- [Runtime Modes for Built-in Tools](../../exec/README-runtime-modes-builtin-tools.md)
- [ISO Runtime](../../exec/README-iso-runtime.md)
- [ReAct Tooling](../agents/react/react-tools-README.md)
