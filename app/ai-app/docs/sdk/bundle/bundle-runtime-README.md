---
id: ks:docs/sdk/bundle/bundle-runtime-README.md
title: "Bundle Runtime"
summary: "Runtime objects and capabilities available inside bundle entrypoints and tools: communicator, integrations, props and secrets, caches, artifacts, and isolated-execution surfaces."
tags: ["sdk", "bundle", "runtime", "tools", "integrations", "communicator", "isolation"]
keywords: ["bundle runtime objects", "communicator access", "integrations access", "props and secrets access", "cache access", "artifact handling", "isolated execution surface", "entrypoint runtime context"]
see_also:
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-chat-stream-events-README.md
---
# Bundle Runtime

This page explains the actual runtime surfaces available to:
- bundle entrypoint code
- bundle-local tools
- tool code running in isolated execution

Use this together with:
- [Bundle Lifecycle](bundle-lifecycle-README.md) for phase ordering
- [Bundle Agent Integration](bundle-agent-integration-README.md) for React, tools/skills, MCP, and Claude Code wiring
- [Bundle Platform Integration](bundle-platform-integration-README.md) for public entrypoint design
- [Tool Subsystem](../tools/tool-subsystem-README.md) for descriptor and execution internals

## Mental model

There are two different runtime surfaces:

1. bundle entrypoint/runtime surface
   - `self.comm`
   - `self.comm_context`
   - `self.bundle_props`
   - `get_secret_async(...)` / `get_user_secret_async(...)`
   - compatibility sync helpers such as `get_secret(...)` / `get_user_secret(...)`
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
  - `await get_secret_async("b:...")` for current bundle deployment secrets
- `await get_secret_async("...")` / `await get_secret_async("a:...")` for platform/global secrets
- `await get_user_secret_async(...)` for current-user secrets
- the sync helpers remain available for compatibility, but async entrypoints,
  APIs, hooks, cron handlers, `@on_job`, and tools should use the async helpers

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

## Local bundle storage helpers

If bundle code needs local filesystem state on the proc instance, use the SDK helper.

Do not do this:
- build ad hoc paths under the repo checkout
- store mutable runtime state next to bundle source files
- assume the current working directory is durable

Use the canonical bundle local root:

1. `self.bundle_storage_root()`
- available on bundle entrypoints
- resolves the bundle-scoped shared local storage root for the active tenant/project
- stable by bundle id for the active tenant/project

2. `bundle_storage_dir(...)`
- import from `kdcube_ai_app.infra.plugin.bundle_storage`
- use it only in lower-level helpers that do not have an entrypoint instance

Typical pattern for mutable local runtime state:

```python
storage_root = self.bundle_storage_root()
local_root = storage_root / "_subsystem_name"
local_root.mkdir(parents=True, exist_ok=True)
```

Why this matters:
- local mode uses a dedicated mounted bundle-storage folder
- cloud mode uses the shared instance-visible storage root as well
- the helper gives bundle code the platform-managed location instead of an accidental repo-relative path

Use cases for the local helper root:
- cloned repos or working copies
- prepared indexes
- local mirrors
- cron job state
- daily pipeline workspace/cache

Do not confuse this with `AIBundleStorage`:
- local helper root = shared instance-local filesystem
- `AIBundleStorage` = backend storage API for bundle artifacts

## Async props and secrets access

KDCube bundle execution is async. In async bundle code, use the async secret
helpers as the normal API:

```python
from kdcube_ai_app.apps.chat.sdk.config import (
    get_secret_async,
    get_user_secret_async,
    set_user_secret_async,
    delete_user_secret_async,
    set_bundle_secret,
)

deployment_token = await get_secret_async("b:integrations.telegram.bot_token")
user_token = await get_user_secret_async("email.accounts.google_1.tokens")
await set_user_secret_async("email.accounts.google_1.tokens", token_json)
await delete_user_secret_async("email.accounts.google_1.tokens")
await set_bundle_secret("integrations.telegram.webhook_secret", webhook_secret)
```

Scope resolution:

- `b:...` resolves through the current bound bundle context
- user-scoped helpers resolve `user_id` and `bundle_id` from the current
  request context unless passed explicitly
- background jobs restore the same request/runtime context snapshot before
  calling bundle code, so job tools can read user-scoped secrets without
  inventing ids in the LLM prompt

Compatibility:

- `get_secret(...)`, `get_user_secret(...)`, `set_user_secret(...)`, and
  `delete_user_secret(...)` still exist for old sync code
- do not use sync helpers in new async request paths unless the surrounding
  code is already sync-only
- do not call `get_secrets_manager(...).get_secret(...)` directly from bundle
  or feature code

Provider behavior:

- `secrets-service` uses native async HTTP calls
- in-memory secrets are immediate
- file-backed and AWS SM providers expose async helper methods through a
  compatibility offload because their current storage, Redis-lock, and boto
  clients are sync internally
- the public bundle contract is still async: callers should `await` the helper
  instead of blocking the event loop directly

## AWS and IAM scoping

Bundles run in the same processor process, so AWS identity must be scoped by
the boto client/session that performs the operation. Do not treat a bundle
configuration value such as `aws_profile` as a process-wide IAM context.

Best practice:

- create explicit `boto3.Session(...)` / `aioboto3.Session(...)` objects for
  bundle-specific AWS work
- pass `profile_name`, `region_name`, or assumed-role credentials into that
  session/client explicitly
- keep those sessions local to the operation or bundle component that needs
  them
- do not call `boto3.setup_default_session(...)` from bundle code
- do not mutate `AWS_PROFILE`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, or related
  process environment variables at runtime

Why this matters:

- a scoped `boto3.Session(profile_name="...")` does not change other bundles
- direct `boto3.client(...)` calls use the process default credential chain
- platform SDK helpers use their own configuration, not arbitrary bundle props
- a later bundle-specific session does not re-scope clients already created by
  another bundle or by the platform

`AIBundleStorage` is platform storage. It uses its explicit `storage_uri` or
the platform `KDCUBE_STORAGE_PATH` / `settings.STORAGE_PATH`. Bundle-specific
props such as `my_feature.aws_profile` do not automatically apply to
`AIBundleStorage`. If a bundle needs artifact storage under a non-default AWS
identity, pass storage configuration explicitly through the storage API or add a
bundle-owned storage wrapper that constructs the backend with an explicit
profile/region.

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

Reusable helper:

- `kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context.scope()` returns the
  current tenant, project, bundle id, bundle user scope, user type,
  conversation/turn ids, bundle props, storage root, output/work dirs, and
  `bundle_call_context`.
- The returned `user_id` is the bundle user scope. It is not guaranteed to be a
  KDCube account id; public integrations may resolve stable external identities.
- Use `bundle_call_context` for runtime ids that tools need but the model should
  not provide, such as task id, execution id, job metadata, or provider context.
- `kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context.host_files(...)`
  hosts current-turn files through the active conversation store and returns the
  strict `artifact_type: "files"` result payload. Use it only from trusted
  bundle/catalog tools after the file has been written or materialized.
- `host_files(...)` requires prepared tool context: an active `ToolSubsystem`
  with a hosting service, communicator scope for tenant/project/user/
  conversation/turn, conversation storage, and a readable output directory.
  `BaseWorkflow.build_react(...)` prepares this for normal tool calls, and
  `BaseWorkflow.rebind_request_context(...)` refreshes it for cached workflow
  instances.
- if that context is not prepared, `host_files(...)` raises a runtime error such
  as `tools are not bound to the current tool subsystem`,
  `tool hosting service is unavailable`, `tool communicator is unavailable`, or
  `bundle storage root is unavailable`.

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
6. the trusted runtime rebuilds conversation hosting from runtime storage
   settings and attaches it to the isolated `ToolSubsystem`
7. `bootstrap_bind_all(...)` is the SDK utility that performs this isolated
   runtime preparation; custom isolated runners must call it or provide an
   equivalent prepared tool context before trusted tools can call
   `host_files(...)`

Important:
- isolated execution does not inherit arbitrary live Python objects
- it receives a reconstructed narrow runtime contract
- tool code should therefore rely on the documented bound surfaces, not on
  random global host state
- generated executor code should call catalog tools through
  `agent_io_tools.tool_call(...)` when it needs trusted capabilities such as
  mailbox access, file materialization, or `host_files(...)`

## Runtime surface matrix

| Surface | Bundle entrypoint: chat turn | Bundle entrypoint: REST op | Tool module: in proc | Tool module: isolated |
| --- | --- | --- | --- | --- |
| `self.comm` | yes | yes | no | no |
| `self.comm_context` | yes | yes | no | no |
| `bundle_props` / `self.bundle_prop(...)` | yes | yes | indirectly through bundle code only | indirectly through bundle code only |
| `get_secret_async(...)` / `get_secret(...)` | yes | yes | yes, if imported directly | yes, if imported directly |
| `get_user_secret_async(...)` / `get_user_secret(...)` | yes | yes | yes, if imported directly | yes, if imported directly |
| bundle storage helpers | yes | yes | yes if the tool receives/constructs the needed bundle context | yes if the tool receives/constructs the needed bundle context |
| `_SERVICE` / `SERVICE` | no | no | yes | yes |
| `_INTEGRATIONS` / `INTEGRATIONS` | no | no | yes | yes |
| `_COMMUNICATOR` / `COMMUNICATOR` | no | no | yes | yes |
| `get_comm()` | yes, indirectly | yes, indirectly | yes | yes |
| `_KV_CACHE` / `KV_CACHE` | no | no | yes when configured | yes when configured |
| `_CTX_CLIENT` / `CTX_CLIENT` | no | no | yes when available | yes when available |
| `OUT_DIR` / `WORKDIR` | only inside isolated exec code paths | only inside isolated exec code paths | only when the tool is running inside an execution context | yes |
| `bundle_tool_context.host_files(...)` | no | no | yes in trusted catalog tools | yes in trusted supervisor/runtime catalog tools |

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

For actual event names, `chat.delta` shape, and built-in markers such as
`answer`, `thinking`, `canvas`, `timeline_text`, and `subsystem`, read:

- [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md)

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
- In async entrypoints, APIs, hooks, cron handlers, `@on_job`, and tools, use
  async secret helpers such as `get_secret_async(...)` and
  `get_user_secret_async(...)`.
- Tool modules should use the centrally bound runtime globals rather than trying
  to reconstruct runtime state themselves.
- Use `get_comm()` or `_COMMUNICATOR` when a tool needs chat-side event
  emission.
- If you want a REST-triggered bundle/tool event to reach the initiating peer
  only, make sure the request carries the `KDC-Stream-ID` header for that
  connected peer.
- Assume isolated runtime reconstructs only the documented portable contract.
