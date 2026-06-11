---
id: ks:docs/sdk/solutions/event-hub/design/resolver-directory-and-operation-routing-README.md
title: "Resolver Directory And Operation Routing Design"
summary: "Design for fast cross-bundle object resolver discovery using a Redis TTL directory, local-first dispatch, direct resolver operation calls, temporary blob exchange, and Data Bus only for durable/asynchronous coordination."
status: design
tags: ["sdk", "solutions", "event-hub", "design", "resolvers", "redis", "data-bus", "namespaces", "canvas", "widgets"]
updated_at: 2026-06-08
keywords:
  [
    "resolver directory",
    "resolver heartbeat",
    "cross bundle resolver",
    "resolver operation routing",
    "redis resolver registry",
    "temporary blob store",
    "canvas pin actions",
    "object resolver",
    "namespace owner",
    "data bus resolver role",
  ]
see_also:
  - ks:docs/sdk/namespace-services/providers-README.md
  - ks:docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - ks:docs/sdk/solutions/canvas/pin-integration-README.md
  - ks:docs/sdk/events/namespaces-README.md
  - ks:docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - ks:docs/service/comm/data-bus-README.md
  - ks:docs/sdk/bundle/bundle-subsystem-integration-README.md
---
# Resolver Directory And Operation Routing Design

This is a design document for resolver directory mechanics under the broader
[Namespace Services: Providers](../../../namespace-services/providers-README.md)
concept. It is not the current runtime contract.

The current implemented mode is local Python import plus explicit resolver
registration inside a composition bundle. That local path stays valid and
remains the first dispatch path. This design adds a second path for resolver
owners that are alive in other bundles or runtime processes.

## Problem

Canvas, chat, and other UI surfaces need to operate on objects owned by many
subsystems:

```text
task:issues/...
mem:...
fi:conv_.../turn_.../outputs/report.md
ks:docs/...
cnv:.../ut_...
```

The surface that displays or pins an object should not own the object's
semantics. A canvas card for `task:issues/...` should not know task storage. A
chat widget that displays `mem:...` should not implement memory preview. A
download button for `fi:...` should go to the ReAct/platform artifact resolver,
not to a canvas-specific file path.

The first cross-bundle idea was request/reply over the Data Bus:

```text
caller -> data bus request -> resolver owner -> data bus reply
```

That is too slow and too indirect for latency-sensitive actions such as
`preview`, `open`, and `download`. It also makes simple object actions depend
on stream workers and reply timing.

## Design Goal

Use a hybrid resolver path:

```text
1. Local registry/decorators.
2. Redis TTL resolver directory.
3. Direct resolver operation call on the owner bundle.
4. Temporary blob refs for large request/response payloads.
5. Data Bus only for durable mutations, async work, progress, and UI fanout.
```

The expected fast path:

```text
canvas card action
  -> local resolver registry lookup
  -> if not found, Redis resolver directory lookup
  -> direct resolver_execute operation on owner bundle
  -> bounded response
```

The Data Bus remains important, but it is not the default RPC path for
resolver operations.

## Non-Goals

- Do not replace local resolver imports. Local registration is still the
  fastest and most testable path for composed bundles.
- Do not make canvas a universal resolver. Canvas stores pins and routes
  actions; namespace owners resolve objects.
- Do not store browser download handles, `rn`, or transport URLs as object
  identity. Store canonical logical refs only.
- Do not assume filesystem `/tmp` is shared across distributed runtimes.
- Do not use Data Bus request/reply for every `preview` or `open` action.

## Conceptual Planes

```text
control plane:
  resolver owner publishes a live descriptor with TTL
  resolver owner refreshes descriptor as heartbeat

data plane:
  caller resolves object action through local registry or directory lookup
  caller invokes owner operation directly
  large inputs/outputs pass through temporary blob refs

event plane:
  Data Bus carries durable mutations, async operation progress,
  cross-widget open notifications, and refresh events
```

## Ownership Model

| Namespace | Owner | Resolver should live with |
| --- | --- | --- |
| `fi:` | ReAct/platform artifact system | ReAct event/artifact module |
| `mem:` | Memory subsystem | SDK memory module |
| `task:` | Task/issue subsystem | Task solution or bundle task domain |
| `ks:` | Knowledge subsystem | Knowledge bundle/module |
| `cnv:` | Canvas subsystem | SDK canvas module |
| `ext:` | Existing bundle-hosted external artifacts | The bundle/module that minted the ref |

The resolver owner defines supported operations and default semantics. A
composition bundle registers or discovers that owner. It does not reimplement
the owner's logic.

## High-Level Architecture

```text
                                      Redis TTL directory
                                      kdcube:resolver:...
                                             ▲
                                             │ heartbeat
                                             │
        local import path                    │
┌─────────────────────────┐          ┌─────────────────────────┐
│ caller bundle           │          │ resolver owner bundle    │
│ canvas/chat/task UI     │          │ task/memory/react/etc.   │
│                         │          │                         │
│ local resolver registry │          │ @object_resolver(...)    │
│ resolver client         │          │ resolver_execute op      │
└─────────────┬───────────┘          └─────────────▲───────────┘
              │                                    │
              │ local miss                         │ direct call
              ▼                                    │
       Redis directory lookup ─────────────────────┘
```

If the resolver owner is local, no Redis lookup or network hop is needed.

## Redis Directory

Resolver owners publish records into Redis with TTL. A missing or expired
record means the resolver is not live.

Recommended key:

```text
kdcube:resolver-registry:{tenant}:{project}:{namespace}:{resolver_id}
```

Examples:

```text
kdcube:resolver-registry:demo-tenant:demo-project:task:task_tracker.issue_story
kdcube:resolver-registry:demo-tenant:demo-project:mem:sdk.memory
kdcube:resolver-registry:demo-tenant:demo-project:fi:react.event_ref
```

Value schema:

```json
{
  "schema": "kdcube.resolver.registration.v1",
  "tenant": "demo-tenant",
  "project": "demo-project",
  "bundle_id": "task-tracker@1-0",
  "resolver_id": "task_tracker.issue_story",
  "namespace": "task",
  "label": "Task Issue Story",
  "operations": {
    "capabilities": {"mode": "sync"},
    "describe": {"mode": "sync"},
    "preview": {"mode": "sync", "max_inline_bytes": 32768},
    "open": {"mode": "sync_returns_ui_event"},
    "download": {"mode": "not_supported"},
    "rehost": {"mode": "sync_or_async"}
  },
  "operation": {
    "bundle_id": "task-tracker@1-0",
    "alias": "resolver_execute",
    "route": "operations",
    "transport": "bundle_operation"
  },
  "priority": 100,
  "heartbeat_at": "2026-06-08T10:00:00Z",
  "expires_at": "2026-06-08T10:01:00Z",
  "owner_instance": "proc-1",
  "version": "2026.6.8"
}
```

TTL guidance:

| Setting | Suggested value | Notes |
| --- | ---: | --- |
| `ttl_seconds` | 60 | Resolver disappears quickly when owner stops. |
| `heartbeat_interval_seconds` | 15-20 | Refresh several times within TTL. |
| `stale_after_seconds` | 2 * heartbeat interval | Prefer fresher records when multiple exist. |

The registration should be idempotent. Refreshing the descriptor should replace
the value and extend the TTL.

## Resolver Declaration

The owner domain should be able to declare a resolver with a decorator. Exact
API names are design placeholders.

```python
from kdcube_ai_app.apps.chat.sdk.solutions.event_hub.resolvers import (
    object_resolver,
)

@object_resolver(
    namespace="task",
    resolver_id="task_tracker.issue_story",
    operations={
        "capabilities": {"mode": "sync"},
        "preview": {"mode": "sync", "max_inline_bytes": 32768},
        "open": {"mode": "sync_returns_ui_event"},
        "rehost": {"mode": "sync_or_async"},
    },
)
class TaskIssueResolver:
    async def capabilities(self, ctx, object_ref, request):
        ...

    async def preview(self, ctx, object_ref, request):
        ...

    async def open(self, ctx, object_ref, request):
        ...

    async def rehost(self, ctx, object_ref, request):
        ...
```

Bundle load should discover these declarations the same way bundle loader
discovers tools, APIs, event policies, and Data Bus handlers.

## Resolver Execute Operation

Each resolver-owning bundle should expose one stable operation:

```text
resolver_execute
```

This operation receives an object ref plus requested operation and dispatches
to the resolver owner. The operation is direct and bounded. It is not a stream
worker request by default.

Input:

```json
{
  "schema": "kdcube.resolver.execute.request.v1",
  "namespace": "task",
  "resolver_id": "task_tracker.issue_story",
  "operation": "preview",
  "object_ref": "task:issues/issue_2026-06-08-100000",
  "request": {
    "max_inline_bytes": 32768,
    "surface": "canvas",
    "card_id": "T_2026-06-08-100001"
  },
  "input_ref": null,
  "caller": {
    "bundle_id": "some-chat@1-0",
    "surface": "canvas"
  }
}
```

Output:

```json
{
  "schema": "kdcube.resolver.execute.result.v1",
  "ok": true,
  "namespace": "task",
  "resolver_id": "task_tracker.issue_story",
  "operation": "preview",
  "object_ref": "task:issues/issue_2026-06-08-100000",
  "mime": "application/json",
  "result": {
    "title": "Batch ReAct Actions validation",
    "summary": "ReAct harness validates batch tool calls..."
  }
}
```

The result must echo `object_ref`. The resolver must not replace object
identity with a transport URL.

## Operation Modes

| Mode | Meaning | Example |
| --- | --- | --- |
| `sync` | Direct bounded result. | `capabilities`, `describe`, small `preview`. |
| `sync_returns_ui_event` | Direct result contains a UI event request. | `open task`, `open memory`. |
| `sync_or_blob` | Inline if small, blob ref if large. | `download`, large preview. |
| `sync_or_async` | Direct for small work, Data Bus job for long work. | `rehost` large files. |
| `not_supported` | Hide or disable operation. | `download` on `task:` issue. |

The caller uses operation metadata for routing and UX hints. The resolver
still authoritatively validates the requested operation.

## Local-First Lookup Algorithm

```text
resolve_object_action(object_ref, operation, request):
  namespace = prefix_before_colon(object_ref)

  local = local_registry.find(namespace, operation)
  if local:
    return local.execute(object_ref, operation, request)

  records = resolver_directory.lookup(namespace, tenant, project)
  candidates = filter records where:
    - TTL alive
    - operation supported
    - auth/visibility allows caller
    - resolver accepts object_ref shape

  if no candidates:
    return unresolved(namespace, object_ref, operation)

  owner = select by:
    - highest priority
    - freshest heartbeat
    - stable resolver_id tie-break

  if operation mode is sync/sync_returns_ui_event/sync_or_blob:
    return call_bundle_operation(owner.operation, request)

  if operation mode is async:
    return submit_data_bus_job(owner, request)
```

The first local path is important for composed bundles such as task-tracker
today. It keeps simple cases fast and debuggable.

## Direct Operation Call

The resolver directory record points to a bundle operation. The caller should
use the existing authenticated bundle operation mechanism when the resolver
owner is remote.

```text
caller resolver client
  -> /api/integrations/bundles/{tenant}/{project}/{owner_bundle}/operations/resolver_execute
  -> owner bundle resolver dispatcher
  -> resolver method
  -> bounded result
```

The operation must run under the original user context. The resolver owner
must receive enough actor information to enforce permissions.

Do not call bundle internals directly across process boundaries.

## Temporary Blob Store

Resolver operations may need to exchange large inputs or outputs. Do not
assume `/tmp` is shared. In local development, the implementation may be
file-backed. In distributed deployment, it must use a shared backend.

Expose a logical temporary ref:

```text
tmp:{tenant}:{project}:{blob_id}
```

Blob metadata:

```json
{
  "schema": "kdcube.resolver.tmp_blob.v1",
  "tmp_ref": "tmp:demo-tenant:demo-project:blob_2026-06-08-100000",
  "owner_bundle_id": "task-tracker@1-0",
  "created_by_user_id": "user_123",
  "mime": "application/pdf",
  "size": 932182,
  "sha256": "...",
  "expires_at": "2026-06-08T10:10:00Z",
  "storage": {
    "kind": "bundle_artifact_storage",
    "key": "resolver-tmp/blob_..."
  }
}
```

Use blob refs when:

- input data exceeds inline request bounds;
- output bytes exceed inline response bounds;
- response should be downloadable by the browser;
- resolver owner needs to stage transformed bytes before rehost.

TTL guidance:

| Blob type | Suggested TTL |
| --- | ---: |
| preview result | 5-10 minutes |
| download response | 10-30 minutes |
| rehost input | until operation completes plus short grace |
| failed operation diagnostic | 5-10 minutes |

The resolver result should include `output_ref` when data is not inline:

```json
{
  "ok": true,
  "operation": "download",
  "object_ref": "fi:conv_.../turn_.../outputs/report.pdf",
  "mime": "application/pdf",
  "output_ref": "tmp:demo-tenant:demo-project:blob_...",
  "filename": "report.pdf",
  "size": 932182
}
```

The caller then decides whether to download, preview, rehost, or attach that
blob according to its own user action.

## Data Bus Role

Data Bus is not the default resolver RPC path. Use it when the operation is a
durable domain message, asynchronous, or requires UI fanout.

Use Data Bus for:

```text
canvas.patch
task.patch
task.attach
memory mutation
ui.object.open.requested fanout
resolver async progress
resolver async completion
widget refresh notifications
```

Do not use Data Bus for:

```text
capabilities
describe
small preview
simple direct download preparation
local resolver execution
```

### Open Flow

```text
user clicks Show on task card
  -> canvas_object_action(task:..., open)
  -> resolver client finds task resolver
  -> direct resolver_execute(open)
  -> result contains ui_event
  -> Data Bus or comm relay delivers ui.object.open.requested
  -> task widget handles dirty-state guard
```

The resolver asks the widget to open. The target widget owns the decision when
there are unsaved edits.

### Download Flow

```text
user clicks Download on fi: card
  -> canvas_object_action(fi:..., download)
  -> resolver client finds ReAct artifact resolver
  -> direct resolver_execute(download)
  -> inline bytes or tmp: output_ref
  -> browser downloads through resolver/client response
```

No `rn`, `ef`, or browser route is stored on the canvas card.

### Rehost Flow

```text
user drops fi: artifact onto task attachments
  -> task subsystem calls resolver_execute(fi, download or rehost-source)
  -> task subsystem computes deterministic attachment id
  -> task subsystem stores bytes under task-owned namespace
  -> task.patch/task.attach mutation goes through Data Bus or service path
  -> task widgets receive refresh
```

Pinning is not rehosting. Rehosting is explicit ownership transfer into the
target subsystem.

## Auth And Visibility

Resolver directory records are not a permission grant. They are discovery
metadata.

Every direct operation must enforce:

- tenant/project scope;
- current user identity and role;
- source bundle identity when relevant;
- operation visibility;
- object-level ACLs;
- size and MIME bounds;
- resolver-specific policy.

The resolver owner must not trust that a caller found a directory record. It
must validate the request as if it came from a normal API call.

## Selection And Fallback

More than one resolver may publish support for the same namespace. Selection
must be deterministic.

Suggested ordering:

1. local resolver if present;
2. configured preferred resolver id for the namespace;
3. highest priority;
4. freshest heartbeat;
5. stable lexical resolver id tie-break.

If no resolver can handle the operation:

```json
{
  "ok": false,
  "error": "resolver_not_available",
  "namespace": "mem",
  "operation": "open",
  "object_ref": "mem:mem_..."
}
```

The UI should keep the pin visible and show unresolved/disabled actions. A pin
does not become invalid merely because its resolver is offline.

## Failure Modes

| Failure | Caller behavior |
| --- | --- |
| No local resolver and no live directory record. | Keep object visible; disable action; show resolver unavailable. |
| Directory record exists but operation call fails with 404. | Invalidate cache for that resolver; retry lookup once. |
| Heartbeat stale. | Prefer fresher resolver; if none, return resolver unavailable. |
| Permission denied. | Show access denied; do not remove pin. |
| Large inline response exceeds bounds. | Resolver must return `output_ref` or fail with `response_too_large`. |
| Async operation admitted. | Return job id and progress channel; update UI over Data Bus. |
| Target widget unavailable for `open`. | Return `target_surface_unavailable`; host may mount the widget. |

## Caching

Callers may cache resolver directory lookups briefly, but must honor TTL.

Recommended client-side resolver cache:

```text
key: tenant/project/namespace/operation
ttl: min(record_ttl_remaining, 10 seconds)
invalidate on operation failure, permission change, or bundle reload signal
```

Do not cache resolver operation results unless the resolver explicitly marks a
result cacheable.

## Integration With Canvas

Canvas should depend on a generic resolver client, not on every subsystem.

```text
canvas UI
  -> canvas_object_action
  -> canvas backend
  -> resolver client
     1. local registry
     2. resolver directory
     3. direct resolver operation
  -> result/action UI
```

Canvas storage keeps:

```json
{
  "card_id": "T_2026-06-08-100000",
  "object_ref": "task:issues/issue_2026-06-08-100000",
  "kind": "issue.ref",
  "rect": {"x": 80, "y": 160, "w": 260, "h": 120},
  "description": "",
  "comments": [],
  "display_cache": {
    "title": "Batch ReAct Actions validation",
    "mime": "application/json"
  }
}
```

Canvas storage does not keep:

```text
download URL
browser route
rn/ef handle for fi:
task internals
memory internals
```

## Integration With Chat Widgets

A chat widget can live in one bundle and still resolve objects from other
bundles if it uses the resolver client.

```text
chat widget bundle
  local resolvers:
    fi: if ReAct artifact module is imported

  remote resolvers:
    task: from task bundle directory record
    mem: from memory bundle/module directory record
    ks: from knowledge bundle directory record
```

When a context pin is attached to chat, the chat widget should preserve the
canonical object ref:

```text
mem:...      remains mem:...
task:...     remains task:...
fi:conv_...  remains fi:conv_...
```

ReAct rendering is still handled by event policies. Resolver operations are
for user/UI/object actions. Policies are for model-facing timeline and
ANNOUNCE rendering.

## Resolver Directory Service API

Proposed SDK API:

```python
class ResolverDirectory:
    async def publish(self, registration: ResolverRegistration, *, ttl_seconds: int) -> None:
        ...

    async def heartbeat(self, registration_id: str, *, ttl_seconds: int) -> None:
        ...

    async def lookup(self, *, tenant: str, project: str, namespace: str) -> list[ResolverRegistration]:
        ...

    async def retract(self, registration_id: str) -> None:
        ...
```

Proposed resolver client API:

```python
class ObjectResolverClient:
    async def execute(
        self,
        *,
        object_ref: str,
        operation: str,
        request: dict | None = None,
        input_ref: str | None = None,
        context: ResolverRequestContext,
    ) -> dict:
        ...
```

Proposed bundle helper:

```python
def register_object_resolvers(entrypoint, resolvers: list[ObjectResolver]) -> None:
    ...
```

The helper should:

1. expose local registry entries;
2. publish directory records on bundle load;
3. start heartbeat refresh;
4. retract records on shutdown when possible;
5. expose `resolver_execute` operation.

## Implementation Plan

### Phase 1 - Local Shape Stabilization

- Keep local resolver registry as first dispatch path.
- Move common resolver interfaces to `sdk/solutions/event-hub`.
- Keep namespace owner implementations in their domains:
  - `react/events/resolver.py` for `fi:`;
  - `memory/events/resolver.py` for `mem:`;
  - task domain resolver for `task:`;
  - canvas resolver for canvas-owned refs.
- Add tests that local registry dispatches by namespace and operation.

### Phase 2 - Redis Resolver Directory

- Add registration schema and Redis key helper.
- Add bundle-load publisher for resolver declarations.
- Add heartbeat task with TTL refresh.
- Add lookup client with short cache and stale-record filtering.
- Add diagnostics endpoint or CLI command to list live resolvers.

### Phase 3 - Direct Resolver Execute

- Add standard `resolver_execute` operation helper for resolver owner bundles.
- Add resolver client fallback from local registry to directory lookup.
- Enforce original user context and visibility in direct operation calls.
- Add tests for remote lookup selection and failure invalidation.

### Phase 4 - Temporary Blob Store

- Add `tmp:` ref abstraction with TTL metadata.
- Implement local file-backed storage for local dev.
- Implement shared backend for distributed runtime.
- Add bounds: max inline bytes, max blob bytes, MIME allowlist hooks.

### Phase 5 - Data Bus Integration For Async/UI

- Route `ui.object.open.requested` through Data Bus or comm relay according to
  target scope.
- Add async resolver job conventions for long operations.
- Add progress/completion event shapes.
- Ensure target widgets handle unavailable and dirty states.

### Phase 6 - Canvas And Chat Adoption

- Replace canvas direct namespace branching with generic resolver client.
- Ensure canvas cards keep canonical refs only.
- Ensure chat widget drag/context payloads keep canonical refs only.
- Let a chat bundle resolve object refs from live resolver owners without
  importing every subsystem.

## Test Plan

| Test | Expected result |
| --- | --- |
| Local resolver priority | Local resolver executes without Redis lookup. |
| Directory publish heartbeat | Resolver record appears with TTL and refreshes before expiry. |
| Expired resolver ignored | Lookup does not return stale resolver after TTL. |
| Operation capability filter | Resolver lacking `download` is not selected for download. |
| Direct execute user context | Owner receives original tenant/project/user and enforces ACL. |
| Large result uses blob | Resolver returns `tmp:` when response exceeds inline bound. |
| Missing resolver | Caller returns `resolver_not_available` and keeps pin visible. |
| Open UI event | Resolver returns `ui.object.open.requested`; target widget handles dirty state. |
| Data Bus separation | `preview` does not enqueue Data Bus; `patch` does. |
| Canonical ref preservation | `fi:`, `task:`, `mem:` refs stay unchanged across canvas/chat/context. |

## Open Questions

1. Should resolver directory records be tenant/project scoped only, or also
   environment/cluster scoped?
2. Should resolver priority be configured by deployment props, resolver
   declaration, or both?
3. What shared backend should back `tmp:` in production: bundle artifact
   storage, S3, Redis blobs with size caps, or a dedicated blob service?
4. Should `ui.object.open.requested` always use Data Bus, or should same-page
   widgets use local browser events with Data Bus only for cross-bundle cases?
5. How should resolver directory diagnostics be exposed in the control plane?

## Current Status Marker

As of 2026-06-08:

- local resolver registration is implemented in the task-tracker pilot;
- SDK canvas has a local resolver registry and object action operation shape;
- memory and ReAct artifact resolvers are moving into their owner modules;
- Data Bus exists for durable bundle-scoped messages;
- Redis TTL resolver directory is not implemented;
- cross-bundle resolver discovery is not implemented;
- `tmp:` resolver blob exchange is not implemented.

This document is the design target for the next resolver-directory iteration.
