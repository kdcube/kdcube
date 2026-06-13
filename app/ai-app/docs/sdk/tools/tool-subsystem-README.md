---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
title: "Tool Subsystem"
summary: "Canonical runtime flow for agent-scoped tool wiring: surfaces.as_consumer config, descriptor adapters, dynamic loading, binding, and execution in in-memory and isolated modes."
tags: ["sdk", "tools", "subsystem", "runtime", "descriptor", "isolation", "mcp", "binding"]
keywords: ["surfaces.as_consumer", "agent tool config", "ToolSubsystem", "resolve_codegen_tools_specs", "io_tools.tool_call", "ToolStub", "py_code_exec_entry.py", "rewrite_runtime_globals_for_bundle", "bind_module_target", "_SERVICE", "_INTEGRATIONS"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/block-production-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-runtime-modes-builtin-tools.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
---
# Tool Subsystem

This is the canonical reference for how tool descriptors are consumed and how tool calls execute.

## Agent-scoped tool wiring

The preferred bundle contract for model-callable tools is:

```yaml
surfaces:
  as_consumer:
    default_agent: main
    agents:
      main:
        tools:
          - id: web
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.tools.web_tools
            alias: web_tools
            discovery: semantic_kernel
            allowed: [web_search, web_fetch]
            tool_traits:
              web_search:
                strategy: [exploration]
              web_fetch:
                strategy: [exploration]
            runtime:
              web_search: local
              web_fetch: local

          - id: knowledge
            kind: mcp
            server_id: knowledge
            alias: knowledge
            allowed: ["*"]
            tool_traits:
              "*":
                strategy: [exploration]

          - id: task_service
            kind: named_service
            alias: named_services
            namespaces:
              task:
                allowed:
                  - provider.about
                  - object.list
                  - object.search
                  - object.schema
                  - object.upsert
                  - object.delete
            tool_traits:
              provider_about:
                strategy: [exploration]
              search_objects:
                strategy: [exploration]
              object_schema:
                strategy: [exploration]
              upsert_object:
                strategy: [exploitation]
              delete_object:
                strategy: [exploitation]
```

`surfaces.as_consumer.agents.<agent_id>.tools` is a list. Each list item is one
source connected to that agent. There is no second `tools:` level under the
agent.

This is the consumer-side surface: it says what an agent may call. A bundle that
publishes tools for other consumers should use a separate provider/publication
surface, not an entry under one agent.

Supported `kind` values:

| kind | Meaning |
|---|---|
| `python` | Load a Python tool module or bundle-local `ref`; only Semantic Kernel-decorated tools are intended for model catalogs. |
| `mcp` | Connect a configured MCP server as a tool source. |
| `named_service` | Expose configured named-service operations as generic namespace tools. |

`allowed` is an allow-list of callable names for `python` and `mcp` sources.
For MCP, `["*"]` exposes all tools returned by the server. For Python sources,
prefer explicit callable names.

For `named_service`, `allowed` uses named-service operation ids such as
`object.search` and `object.upsert`; `allowed_operations` remains accepted for
older descriptors. Canvas-only presentation resolution stays under
`surfaces.as_consumer.ui.canvas.resolvers`, not under the agent tool connection.
`object.action` is not exposed to ReAct agents as a normal tool.
Named-service catalog entries render only `namespaces applicable`, so ReAct can
see which configured namespaces support each generic tool without seeing
provider protocol operation ids.

`tool_traits` is per connection and keyed by that connection's callable names.
The runtime qualifies those names with the connection alias and stores them in
tool metadata. The `strategy` trait drives ReAct multi-action compatibility:
`exploration`, `exploitation`, `neutral`, or `unknown` when no trait is present.
MCP connections may use `"*"` as a wildcard trait for all tools from that
server. See
`repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md`.

At runtime, `agent_tool_config_from_bundle_props(...)` converts this config
into:

- module tool specs
- MCP tool specs
- tool runtime overrides
- tool traits
- allowed aliases
- per-alias allowed tool names

ReAct passes both allowed aliases and per-alias allowed tool names to
`ToolSubsystem`. That means a source can be loaded for one agent while another
agent in the same bundle sees a different subset.

## Runtime Wiring

Bundle workflow code resolves the active agent config and passes the resolved
specs to `create_tool_subsystem_with_mcp(...)`:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    agent_tool_config_from_bundle_props,
)

tool_config = agent_tool_config_from_bundle_props(
    self.bundle_props,
    agent_id,
    bundle_root=BUNDLE_ROOT,
    default_agent_id="main",
)

tool_subsystem, _ = create_tool_subsystem_with_mcp(
    service=self.model_service,
    comm=self.comm,
    logger=self.logger,
    bundle_spec=self.config.ai_bundle_spec,
    context_rag_client=self.ctx_client,
    registry={"kb_client": self.kb},
    raw_tool_specs=tool_config.tool_specs,
    tool_runtime=tool_config.tool_runtime,
    mcp_tool_specs=tool_config.mcp_tool_specs,
    mcp_env_json=os.environ.get("MCP_SERVICES") or "",
    tool_traits=tool_config.tool_traits,
)
```

The subsystem does not auto-scan bundle files on disk. The workflow decides what
is loaded. New bundles should keep the authoritative tool policy in
`surfaces.as_consumer.agents.<agent_id>.tools` in bundle config/templates.
`tools.agents` is a legacy fallback for older bundles, not the preferred policy
surface.

## `module` vs `ref` resolution

Resolved Python tool spec entries are portable:
- `module`: importable Python module path.
- `ref`: file path relative to the bundle root.

Resolution flow:
1. `resolve_codegen_tools_specs(...)` rewrites relative `ref` paths using the bundle root.
2. `ToolSubsystem._resolve_tools(...)` turns `module` and `ref` entries into concrete file paths.
3. `ToolSubsystem._load_tools_module(...)` loads each file through the shared dynamic loader.

Implication: `ref` is not host-only. It is bundle-relative and portable across runtimes.

### Relative imports inside `ref` tools

Bundle-local `ref` modules may use normal relative imports when they live under a
package tree with `__init__.py` files.

The loader creates a synthetic package context for file-based modules in both:
- the in-process tool subsystem
- the isolated runtime bootstrap / supervisor path

That means bundle code can look natural:

```python
from ..services.storage import Store
```

instead of manually reconstructing sibling modules with `importlib`.

The bundle-local import-isolation rule still applies. Do not import same-bundle
helpers from top-level roots such as `services`, `tools`, `apps`, or
`resources`; those names are process-global in proc and can collide across
bundles. For bundle-local tools, use `ref` entries rather than `module` entries
so the runtime can keep the tool tied to the bundle root and rewrite paths for
distributed isolated execution.

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

## Central runtime binding

Tool modules are bound centrally by the runtime. The tool module itself does not
need to bootstrap the environment.

Binding happens in:
- `ToolSubsystem` for normal in-process execution
- `bootstrap.py` for isolated execution bootstrap

Both paths use the same binding contract.

### Hooks the runtime will call if present

- `bind_service(svc)`
- `bind_registry(registry)`
- `bind_integrations(integrations)`

### Canonical module globals stamped by the runtime

| Name | Meaning |
| --- | --- |
| `_SERVICE` / `SERVICE` | model service |
| `model_service` | same service under an explicit name |
| `_INTEGRATIONS` / `INTEGRATIONS` | integration map |
| `_TOOL_SUBSYSTEM` / `TOOL_SUBSYSTEM` | current tool subsystem |
| `_COMMUNICATOR` / `COMMUNICATOR` | chat communicator |
| `_KV_CACHE` / `KV_CACHE` | KV cache |
| `_CTX_CLIENT` / `CTX_CLIENT` | context retrieval client |
| `REGISTRY` | workflow-provided registry |

Current `INTEGRATIONS` content is intentionally narrow:
- `ctx_client`
- `kv_cache`
- `tool_subsystem`

Communicator is not carried as a separate ad hoc integration payload. It is
derived from the normal runtime request context and the portable communicator
descriptor.

## Tool IDs and catalog entries

Tool IDs:
- Module tools: `<alias>.<tool_name>`
- MCP tools: `mcp.<alias>.<tool_name>`
- Named-service tools: `<alias>.<generic_tool_name>`, normally
  `named_services.search_objects`

`ToolSubsystem` introspects loaded modules and builds the catalog used by planner/generator prompts.
Dynamic tool metadata is preserved into that catalog. Named-service tools use
this to render scope:

```text
Scope:
    • namespaces applicable: task, memo
```

## Event-source discovery for ReAct

When ReAct runs with `event_source_pipeline.enabled=true`, the `ToolSubsystem`
also builds an `EventSourceSubsystem`. This registry is attached to
`RuntimeCtx.event_sources` and is used by ReAct phases such as
`tool_call_validation`, `block_production`, `timeline_projection`,
`announce_production`, and `compaction_projection`.

Discovery includes:
- all loaded Python tool spec modules;
- built-in ReAct source declarations such as `react.followup`, `react.steer`,
  `react.write`, and `react.memsearch`;
- explicit `event_specs` modules passed when the workflow creates the tool
  subsystem.

Tool functions can declare event-source metadata directly with `@event_source`.
An event module can also provide `list_event_sources()` for declarations that
are separate from the callable tool module.

Inside ReAct, a tool call is a tool-backed event source. Tool execution still
uses the regular tool subsystem. The event-source registry supplies the ReAct
policy plane for that tool occurrence.

If a tool has no matching event-source declaration, ReAct still handles it with
the structured-result default policy pack. That preserves the normal custom
tool behavior:
- JSON/text output becomes an ordinary `tc:<turn>.<call>.result` block;
- `ret.artifact_type == "files"` produces declared file artifacts;
- generic JSON results are not treated as file paths;
- no source-pool rows, snapshot refs, or ANNOUNCE candidates are produced unless
  a policy explicitly adds them.

The runtime identity rule is:

```text
tool_id      == event_source_id
tool_call_id == event_id
```

For direct MCP tools, the local tool subsystem usually cannot discover Python
decorators from the remote MCP server. Those tools therefore use the same
structured-result default unless the bundle wraps the MCP call in a local tool
or supplies an explicit event-source module through `event_specs`.

## Execution path (runtime enforcement)

1. `execution.execute_tool(...)` picks in-memory vs isolated execution (`TOOL_RUNTIME` + default isolation policy).
2. In-memory path (`_execute_tool_in_memory`) resolves callable by alias and executes via `agent_io_tools.tool_call(...)`.
3. Isolated path (`execute_tool_in_isolation`) passes runtime globals to iso runtime.
4. In the limited executor, `agent_io_tools.tool_call(...)` delegates to supervisor via `ToolStub`.
5. Supervisor resolves callable from `TOOL_ALIAS_MAP` and executes through `agent_io_tools.tool_call(...)`.
6. For `mcp.*` IDs, `agent_io_tools.tool_call(...)` routes to `MCPToolsSubsystem.execute_tool(...)`.
7. The trusted runtime bootstrap rebuilds a conversation hosting service and
   attaches it to the runtime `ToolSubsystem`, so catalog tools can use
   `bundle_tool_context.host_files(...)` in both in-process and isolated
   supervisor execution.

## File-producing tool result contract

All tools should return the standard envelope:

```json
{"ok": true, "error": null, "ret": {...}}
```

When a tool intentionally creates files that should be delivered as artifacts,
the file declaration belongs inside `ret`:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "path": "turn_123/outputs/report.pdf",
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "visibility": "external"
      }
    ]
  }
}
```

React v2 and v3 unwrap `{ok, error, ret}` before result handling. If
`ret.artifact_type == "files"`, each declared file is hosted into the
conversation store and emitted as normal artifact metadata.

The declared `path` / `physical_path` must refer to a file accessible from the
current React `OUT_DIR`, typically under `turn_<id>/outputs/...`.

Trusted bundle tools can also call
`kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context.host_files(...)` after
writing files. The helper hosts through the active conversation store, emits
file events, and returns a `ret` payload with `artifact_type: "files"` and
hosted file rows.

`host_files(...)` is part of the trusted tool runtime surface. It is available
to bundle/catalog tools executed:
- in the normal workflow process
- through in-memory tool execution
- in isolated execution on the trusted supervisor/runtime side

The helper only works after the runtime has prepared the tool context. Required
runtime state is:
- an active `ToolSubsystem`
- `ToolSubsystem.hosting_service`
- communicator scope with tenant, project, user id, conversation id, turn id,
  and user type
- conversation storage and a readable current output directory

Normal React workflows prepare that state through `BaseWorkflow.build_react(...)`
and keep it fresh on cached workflows through
`BaseWorkflow.rebind_request_context(...)`. Isolated execution prepares it in
`kdcube_ai_app.apps.chat.sdk.runtime.bootstrap.bootstrap_bind_all(...)`, which
restores context, builds the communicator, recreates the conversation hosting
service, builds the tool subsystem, and binds modules.

If a tool calls `host_files(...)` without that preparation, it raises a runtime
error such as `tools are not bound to the current tool subsystem`,
`tool hosting service is unavailable`, `tool communicator is unavailable`, or
`bundle storage root is unavailable`. Missing tenant/project/user/conversation/
turn scope is also a runtime-preparation defect; it should not be filled in by
the model.

Generated executor code reaches the same capability by calling a catalog tool
through `agent_io_tools.tool_call(...)`. The generated program does not need to
construct conversation storage or hosting objects; it asks a visible catalog
tool to materialize and host the requested files.

## What survives into isolated execution

Isolated execution does not inherit arbitrary live Python objects from the host
process. It reconstructs a narrow portable runtime:

- env passthrough
- selected `ContextVar` state (`run_ctx`, accounting, generic CV snapshot)
- model service
- registry
- communicator from the portable comm descriptor
- integrations payload such as `kv_cache` and `ctx_client`
- tool subsystem from exported runtime globals
- conversation hosting service rebuilt from the runtime storage settings and
  attached to the tool subsystem

The point is to make a tool module see the same canonical binding contract in
both proc and isolated execution, while still keeping the runtime portable.

The hosting service is reconstructed inside the trusted runtime, not shipped as
a live Python object from the host. It uses the runtime `ConversationStore`,
current communicator, and turn scope restored by bootstrap.

## Custom dependencies in tool modules

Tool modules do **not** currently get an automatic per-tool or per-bundle
dependency installation step analogous to bundle `@venv(...)`.

Current practical rule:
- bundle-local tools loaded through resolved tool specs are imported into the current
  interpreter for that execution mode
- in-memory tool execution means imports must resolve in the proc runtime
- isolated tool execution means imports must resolve in the isolated runtime /
  supervisor environment
- restoring bundle files into the isolated runtime does **not** by itself
  install Python dependencies from a bundle-local `requirements.txt`

So today, if a custom tool module imports a third-party package directly, that
package must already exist in the runtime image/interpreter that will execute
the tool.

Current workaround for dependency-heavy leaf work:
- keep the tool itself lightweight
- move the package-heavy operation into a bundle-local helper marked with
  `@venv(...)`
- call that helper from the tool

That pattern is workable because `@venv(...)` is just a Python execution
boundary, not an HTTP-only surface, but it should still be treated as a leaf
helper boundary:
- pass serializable inputs/outputs only
- do not move communicator / DB / Redis / request-bound runtime objects across
  that boundary
- do not assume proc-side tool bindings exist in the child; `TOOL_SUBSYSTEM`,
  `COMMUNICATOR`, `KV_CACHE`, `CTX_CLIENT`, and similar bound module globals are
  not provided inside the `@venv(...)` subprocess
- expect the helper to create or reuse its own cached subprocess venv

So the current support matrix is:
- direct tool imports of custom deps: supported only when those deps are
  installed in the executing runtime already
- tool-internal calls into bundle `@venv(...)` helpers: supported pattern for
  package-heavy leaf work
- automatic per-tool dependency install from `requirements.txt`: not supported

## Related docs

- [Custom Tools](./custom-tools-README.md)
- [MCP Integration](./mcp-README.md)
- [Runtime Modes for Built-in Tools](../../exec/README-runtime-modes-builtin-tools.md)
- [ISO Runtime](../../exec/README-iso-runtime.md)
- [ReAct Tooling](../agents/react/react-tools-README.md)
