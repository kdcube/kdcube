---
id: ks:docs/sdk/bundle/bundle-runtime-README.md
title: "Bundle Runtime"
summary: "Runtime surfaces available to bundle entrypoints and tool modules across chat turns, REST operations, in-process tools, and isolated execution."
tags: ["sdk", "bundle", "runtime", "tools", "integrations", "communicator", "isolation"]
keywords: ["self.comm", "comm_context", "bundle_props", "get_secret", "_SERVICE", "_INTEGRATIONS", "KV_CACHE", "get_comm", "ToolSubsystem", "bootstrap", "integrations operations", "processor"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-sse-events-README.md
---
# Bundle Runtime

This page explains the actual runtime surfaces available to:
- bundle entrypoint code
- bundle-local tools
- tool code running in isolated execution

Use this together with:
- [Bundle Lifecycle](bundle-lifecycle-README.md) for phase ordering
- [Bundle Platform Integration](bundle-platform-integration-README.md) for public entrypoint design
- [Tool Subsystem](../tools/tool-subsystem-README.md) for descriptor and execution internals

## Mental model

There are two different runtime surfaces:

1. bundle entrypoint/runtime surface
   - `self.comm`
   - `self.comm_context`
   - `self.bundle_props`
   - `get_secret(...)` / `get_user_secret(...)`
   - bundle storage helpers
   - DB/Redis handles passed into the entrypoint

2. tool-module runtime surface
   - `_SERVICE`
   - `_INTEGRATIONS`
   - `_TOOL_SUBSYSTEM`
   - `_COMMUNICATOR`
   - `_KV_CACHE`
   - `_CTX_CLIENT`
   - `REGISTRY`

They are related, but they are not identical.

## Runtime entry paths

### 1) Chat turn path: processor + streaming request

Normal chat turns arrive through ingress, are queued, then executed by the chat
processor.

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py`

Current flow:
1. ingress builds `ChatTaskPayload`
2. processor resolves the bundle from `routing.bundle_id`
3. `get_workflow_instance(...)` creates or reuses the entrypoint
4. `comm_context` is rebound for the current request
5. the runtime calls either:
   - `workflow.<operation>(**params)` when an explicit command is present
   - or `workflow.run(**params)` for the normal turn path

What the bundle has in this path:
- `self.comm`
- `self.comm_context`
- `self.bundle_props`
- `self.pg_pool`
- `self.redis`
- bundle storage helpers such as `bundle_storage_root()`
- secret lookup through:
  - `get_secret("b:...")` for current bundle deployment secrets
  - `get_secret("...")` / `get_secret("a:...")` for platform/global secrets
  - `get_user_secret(...)` for current-user secrets

Communicator behavior in this path:
- if request routing carries an exact socket/stream target, direct peer delivery
  is possible
- otherwise events fan out to all clients connected to the same session room

### 2) REST bundle operation path

Bundle operations invoked by REST currently go through:

`GET|POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{operation}`

Also available for backward compatibility:

`POST /api/integrations/bundles/{tenant}/{project}/operations/{operation}`

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`

Current flow:
1. REST request resolves `bundle_id`
2. runtime creates `ChatTaskPayload`
3. `get_workflow_instance(...)` creates or reuses the entrypoint
4. proc resolves decorated `@api` alias + method first, then falls back to same-name method lookup for undecorated bundles
5. runtime calls the resolved method with `user_id=..., fingerprint=..., **kwargs`

What the bundle has in this path:
- `self.comm`
- `self.comm_context`
- `self.bundle_props`
- `self.pg_pool` / `self.redis` when available
- the same storage and secret helpers as the chat-turn path

Important current communicator rule for REST operations:
- communicator is available
- if the request carries the `KDC-Stream-ID` HTTP header, configurable via
  `STREAM_ID_HEADER_NAME`, meaning the header
  that identifies the connected peer/stream which issued the request, the
  runtime maps that value into `routing.socket_id`
- that lets communicator target the initiating SSE / Socket.IO peer directly
- if the header is absent, the request remains session-scoped

Practical consequence:
- with the `KDC-Stream-ID` peer-identification header, peer-to-peer delivery is
  possible
- without it, all clients listening on that session receive the event
- if nobody is listening on that session, nobody receives it

If the bundle also ships widget or frontend code, that code must follow the
client transport contract when it calls `/api/integrations/*`:

- [bundle-client-communication-README.md](bundle-client-communication-README.md)

### 3) Tool execution in normal in-process runtime

Bundle tools and SDK tools are loaded by `ToolSubsystem`.

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/tool_subsystem.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/tool_module_bindings.py`

What happens:
1. `tools_descriptor.py` is passed into `create_tool_subsystem_with_mcp(...)`
2. `ToolSubsystem` resolves `module` / `ref` entries
3. each tool module is loaded
4. runtime binds service/integrations/registry centrally
5. tool functions are called through `agent_io_tools.tool_call(...)`

What tool modules get:

| Name | Meaning |
| --- | --- |
| `_SERVICE` / `SERVICE` | model service |
| `model_service` | same service under an explicit alias |
| `_INTEGRATIONS` / `INTEGRATIONS` | integration map |
| `_TOOL_SUBSYSTEM` / `TOOL_SUBSYSTEM` | active `ToolSubsystem` |
| `_COMMUNICATOR` / `COMMUNICATOR` | current communicator |
| `_KV_CACHE` / `KV_CACHE` | KV cache |
| `_CTX_CLIENT` / `CTX_CLIENT` | context retrieval client |
| `REGISTRY` | workflow-provided registry |

Current `INTEGRATIONS` contents are:
- `ctx_client`
- `kv_cache`
- `tool_subsystem`

### 4) Tool execution in isolated runtime

When policy or tool runtime sends execution into isolated runtime, the tool
module is not executed in the main process.

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py`

What happens:
1. host runtime serializes a portable spec
2. child runtime restores env and selected `ContextVar` state
3. model service, registry, communicator, and integrations are reconstructed
4. runtime rebuilds the tool subsystem from exported runtime globals
5. tool modules are loaded and bound with the same canonical names

Important:
- isolated execution does not inherit arbitrary live Python objects
- it receives a reconstructed narrow runtime contract
- tool code should therefore rely on the documented bound surfaces, not on
  random global host state

## Runtime surface matrix

| Surface | Bundle entrypoint: chat turn | Bundle entrypoint: REST op | Tool module: in proc | Tool module: isolated |
| --- | --- | --- | --- | --- |
| `self.comm` | yes | yes | no | no |
| `self.comm_context` | yes | yes | no | no |
| `bundle_props` / `self.bundle_prop(...)` | yes | yes | indirectly through bundle code only | indirectly through bundle code only |
| `get_secret(...)` | yes | yes | yes, if imported directly | yes, if imported directly |
| `get_user_secret(...)` | yes | yes | yes, if imported directly | yes, if imported directly |
| bundle storage helpers | yes | yes | yes if the tool receives/constructs the needed bundle context | yes if the tool receives/constructs the needed bundle context |
| `_SERVICE` / `SERVICE` | no | no | yes | yes |
| `_INTEGRATIONS` / `INTEGRATIONS` | no | no | yes | yes |
| `_COMMUNICATOR` / `COMMUNICATOR` | no | no | yes | yes |
| `get_comm()` | yes, indirectly | yes, indirectly | yes | yes |
| `_KV_CACHE` / `KV_CACHE` | no | no | yes when configured | yes when configured |
| `_CTX_CLIENT` / `CTX_CLIENT` | no | no | yes when available | yes when available |
| `OUT_DIR` / `WORKDIR` | only inside isolated exec code paths | only inside isolated exec code paths | only when the tool is running inside an execution context | yes |

## Communicator rules

Communicator is the same core chat communicator used by the rest of the chat
infrastructure.

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/emitters.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/chat.py`

Current routing rules:
- if communicator has `target_sid`, a direct peer event can be delivered to one
  SSE stream / socket
- otherwise events are broadcast to the whole session room

That is why:
- queued chat turns can target one client when the request path carried that
  identity
- REST bundle operations can target one client when the `KDC-Stream-ID` header,
  which identifies the connected peer, is carried with the HTTP request;
  otherwise they broadcast to the session room

For actual event names, `chat.delta` shape, and predefined markers such as
`answer`, `thinking`, `canvas`, `timeline_text`, and `subsystem`, read:

- [bundle-sse-events-README.md](bundle-sse-events-README.md)

## Shared browser, cache, and retrieval from tools

These are the main non-model integrations commonly used by tools:

### KV cache

Read from:
- `_KV_CACHE`
- or `(_INTEGRATIONS or {}).get("kv_cache")`

Use it for:
- small distributed caches
- deduplication flags
- short-lived shared state

### Context retrieval client

Read from:
- `_CTX_CLIENT`
- or `(_INTEGRATIONS or {}).get("ctx_client")`

Use it for:
- context/timeline retrieval
- retrieval-backed tool behavior

### Shared browser

Shared browser is not injected as a bound global. Use the standard shared
service directly:

```python
from kdcube_ai_app.infra.rendering.shared_browser import get_shared_browser

browser = await get_shared_browser()
```

This is the same browser service used by rendering-oriented SDK tools.

## Practical rules

- Entry point code should use `self.comm`, `self.comm_context`, bundle props,
  secrets, and bundle storage helpers.
- Tool modules should use the centrally bound runtime globals rather than trying
  to reconstruct runtime state themselves.
- Use `get_comm()` or `_COMMUNICATOR` when a tool needs chat-side event
  emission.
- If you want a REST-triggered bundle/tool event to reach the initiating peer
  only, make sure the request carries the `KDC-Stream-ID` header for that
  connected peer.
- Assume isolated runtime reconstructs only the documented portable contract.
