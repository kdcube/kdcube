---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
title: "Namespace Services: Providers"
summary: "Transport-neutral SDK concept for bundles and platform subsystems that publish namespace service provider surfaces: namespace ownership, object operations, resolvers, capabilities, relations, and integrations over API, MCP, Data Bus, or local adapters."
status: design
tags: ["sdk", "namespace-services", "named-service-provider", "services", "namespaces", "objects", "resolvers", "mcp", "api", "data-bus", "bundles"]
updated_at: 2026-06-11
keywords:
  [
    "named service provider",
    "named service client",
    "namespace owner",
    "object_ref",
    "object action",
    "provider surface",
    "client surface",
    "transport-neutral service contract",
    "mcp object operations",
    "api object operations",
    "data bus object command",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Namespace Services: Providers

A named service provider is the SDK contract for a bundle or platform subsystem
that exposes a named semantic service to other parts of KDCube.

The provider may own one or more logical reference namespaces, object families,
relations between objects, namespace-level commands, or integration functions.
Namespace ownership is the first major use case, but the abstraction is broader
than object CRUD. A **namespaced service** is a named service provider that owns
or primarily operates on one or more logical namespaces such as `task:` or
`mem:`.

Examples:

| Provider | Typical owned refs or surface |
| --- | --- |
| task issue provider | `task:issues/...`, issue search, issue editor actions |
| memory provider | `mem:...`, memory search, memory viewer actions |
| canvas provider | `cnv:...`, board patches, pins, board object actions |
| ReAct artifact provider | `fi:...`, artifact preview/download/rehost |
| document/source provider | provider-owned refs such as `docs:...` or `repo:...`, document/source search and read actions |

Use **named service provider** for the top-level concept. Use **namespaced
service** for the namespace-owning subtype. Use **object resolver** for one
operation family inside a provider. Use **scene surface** for mounted UI
iframe/widget targets.

## Mental Model

```text
owner bundle/subsystem
  NamedServiceProvider
    provider.about
    provider.capabilities
    object.list / search / get / schema / upsert / delete
    object.action / resolve
    relation.list / search
    event.resolve / event.action
    block.produce / block.render
    provider.operation
        |
        +-- local adapter
        +-- API adapter
        +-- MCP adapter
        +-- Data Bus adapter

caller bundle/widget/client runtime/scene
  NamedServiceClient
    resolves object_ref, namespace, or provider name
    chooses allowed transport
    carries AuthContext from request, Data Bus actor, or headless job
    receives bounded semantic result
```

The client asks the owner. A canvas card that stores `task:issues/BUG-123`
keeps that ref intact and asks the task issue provider to preview, open, edit,
or delete it. Canvas owns board layout; the task provider owns task meaning.

Tools, canvas object actions, chat context chips, ReAct block production,
timeline rendering, artifact/event ref resolution, MCP tools, Codex tools, and
Claude Code tools are consumers of the same provider. Do not create a separate
tool-only contract for a domain that already owns a named service provider.

Ingress is one way to create a caller context. It is not the only way to reach
a provider. A cron job, Data Bus handler, local bundle workflow, MCP request,
or API operation can all call the same provider through a `NamedServiceClient`
once they have a valid `AuthContext`.

## Provider Surface

A provider exposes operations grouped by scope.

### Provider Operations

| Operation | Purpose |
| --- | --- |
| `provider.about` | Describe the provider, owned namespaces/object families, labels, and human-facing purpose. |
| `provider.capabilities` | Report supported operations, transports, object kinds, actions, limits, and policy hints. |
| `provider.operation` | Invoke a provider-level command that is not tied to one object, such as sync, import, rebuild index, or connect integration. |

`provider.about` lets a client understand what the provider is for
before choosing a narrower operation.

Providers that expose mutation should include a concise service description,
base object summaries, and object schema availability in `provider.about`.

### Object Operations

| Operation | Purpose |
| --- | --- |
| `object.list` | Browse objects in a collection with pagination. |
| `object.search` | Search objects; default mode is hybrid when the provider supports it. |
| `object.get` | Fetch one object by `object_ref` or owner-local id. |
| `object.schema` | Return provider-defined object schemas and tool payload guidance for one object kind or ref. |
| `object.upsert` | Create or update one object with idempotency and revision checks. |
| `object.delete` | Delete or archive one object with revision checks. |
| `object.action` | Run a bounded action on an object, such as `preview`, `open`, `download`, `pin`, `rehost`, or provider-defined actions. |
| `object.resolve` | Normalize a ref into a canonical object descriptor and optional `ret.ui_event` or `ret.extra` hints. |

### Relation Operations

| Operation | Purpose |
| --- | --- |
| `relation.list` | List known relations for one object or object family. |
| `relation.search` | Search or filter relations across owned and referenced namespaces. |

Relation operations allow one provider to connect multiple owned objects or
report relationships to objects in other namespaces without taking ownership of
those foreign refs.

### Event And Block Operations

| Operation | Purpose |
| --- | --- |
| `event.resolve` | Resolve an event ref or owner event payload into a semantic descriptor. |
| `event.action` | Run a bounded action on an event ref, such as preview, open, rehost, or explain. |
| `block.produce` | Produce model-visible blocks from provider-owned objects or events. |
| `block.render` | Render provider-owned objects/events for timeline, compact history, widgets, or ANNOUNCE-style summaries. |

These operations are the provider-side shape behind event-source readers,
block-production policies, timeline projection policies, and renderer-specific
resolvers. A ReAct policy may call the provider through a local resolver, API
adapter, or future resolver directory; the operation semantics stay the same.

## Standard Request Fields

All operations receive a context from the runtime and a transport-neutral
request payload.

```python
auth = AuthContext.from_current_request_context()
ctx = NamedServiceContext.from_auth_context(auth)
```

Object-oriented operations use these common request fields:

```json
{
  "schema": "kdcube.named_service.request.v1",
  "provider": "task.issue",
  "namespace": "task",
  "object_ref": "task:issues/BUG-123",
  "object_id": "BUG-123",
  "collection": "issues",
  "cursor": null,
  "limit": 50,
  "query": "blocked auth bug",
  "search_mode": "hybrid",
  "filters": {},
  "sort": [],
  "include": [],
  "action": "open",
  "object": {},
  "base_revision": null,
  "idempotency_key": "client-op-01HX",
  "context": {}
}
```

Only fields relevant to the operation are required. Providers validate the
payload and enforce ownership.

## Standard Response Fields

Responses are bounded and semantic.

```json
{
  "ok": true,
  "ret": {
    "attrs": {
      "provider": {
        "bundle_id": "task-tracker@1-0",
        "provider_id": "task.issue"
      },
      "namespace": "task",
      "object_ref": "task:issues/BUG-123",
      "next_cursor": null,
      "revision": "rev-7",
      "capabilities": {},
      "relations": [],
      "warnings": []
    },
    "object": {},
    "items": [],
    "extra": {},
    "ui_event": null
  },
  "error": null
}
```

Large bytes, long reports, and generated artifacts should be returned as refs or
hosted files, not as unbounded inline response payloads.

When an owned namespace contains attachment refs, implement a `rehost` action
that returns a streaming response. ReAct materializes these refs through the
configured namespace rehoster; MCP-style metadata operations should not base64
large files into tool results.

## Object Actions And UI Routing

`object.action` is the operation family that powers canvas cards, chat context
chips, scene summons, and widget-focused opens.

Example:

```json
{
  "provider": "task.issue",
  "namespace": "task",
  "object_ref": "task:issues/BUG-123",
  "action": "open",
  "context": {
    "source_surface": "sdk.canvas.pinboard"
  }
}
```

Typical response:

```json
{
  "ok": true,
  "ret": {
    "attrs": {
      "namespace": "task",
      "object_ref": "task:issues/BUG-123"
    },
    "ui_event": {
      "type": "kdcube.ui.object.open.requested",
      "subject": "ui.object.open.requested",
      "target_surface": "task_tracker.issue_editor",
      "object_ref": "task:issues/BUG-123",
      "mode": "focus",
      "params": {
        "issue_id": "BUG-123"
      }
    }
  },
  "error": null
}
```

The scene host routes `target_surface` to a mounted widget through its local
surface registry. The provider decides the target and parameters; the scene host
decides how to mount or focus the iframe.

## Provider Declaration

The SDK package lets a bundle declare a provider once and expose that provider
through enabled transports.

```yaml
named_service_providers:
  - provider_id: task.issue
    namespace: task
    refs:
      - task:issues/*
    object_kinds:
      - task.issue
    operations:
      provider.about:
        transports: [local, api, mcp]
      provider.capabilities:
        transports: [local, api, mcp]
      object.list:
        transports: [local, api, mcp]
      object.search:
        transports: [local, api, mcp]
      object.get:
        transports: [local, api, mcp]
      object.schema:
        transports: [local, api, mcp]
      object.upsert:
        transports: [local, api, mcp, data_bus]
      object.delete:
        transports: [local, api, mcp, data_bus]
      object.action:
        transports: [local, api, mcp, data_bus]
      object.resolve:
        transports: [local, api, mcp]
      relation.list:
        transports: [local, api, mcp]
      event.resolve:
        transports: [local, api, mcp]
      event.action:
        transports: [local, api, mcp]
      block.produce:
        transports: [local, api]
      block.render:
        transports: [local, api]
```

The stable concept name is named service provider. The current SDK package
shape is:

```text
kdcube_ai_app/apps/chat/sdk/solutions/named_services_providers/
  types.py
  provider.py
  registry.py
  discovery.py
  client.py
  canvas_resolver.py
  transports/
    api.py
    api_client.py
```

## Bundle Configuration Surface

Named services are configured under one bundle prop root:

```yaml
named_services:
  namespaces:
    task:
      clients:
        default_client:
          tools:
            allowed_operations:
              - provider.about
              - object.list
              - object.search
              - object.get
              - object.schema
              - object.upsert
              - object.delete
        canvas:
          resolver:
            enabled: true
```

`named_services.namespaces` is bundle-level: it declares which namespaces this
bundle consumes and which clients may use model-callable tools. Provider
location is normally resolved from Named Service Discovery.

Provider bundles register their available providers into the Redis-backed
tenant/project discovery table when the bundle is loaded and ready:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    RedisNamedServiceDiscovery,
)

discovery = RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)
await discovery.register_registry(
    self.named_services(),
    bundle_id="task-tracker@1-0",
    transport="bundle_registry",
    registry_method="named_services",
)
```

Named Service Discovery is a provider index. It can contain multiple providers
for the same namespace, including providers from different bundles. Each entry
advertises operations, object kinds, and ref patterns; the runtime chooses the
provider per request.

`named_services.namespaces.<namespace>.clients.<client_id>.tools` is
client-level: it controls which provider operations become model-callable tools
for a specific named-service client surface. A bundle can expose the same
namespace to canvas/chat resolvers while only one client receives
model-callable tools for it.

Client ids are not ReAct-specific. The same section can describe tool access
for ReAct agents, Claude Code, Codex, MCP, widget, job, or other client
runtimes once their adapters consume the provider contract. When the client is
a ReAct agent, the client id is the agent id; it still lives under
`clients`, because the namespace is not exclusively an agent configuration
surface.

When a client must pin provider endpoints instead of using discovery, provider
endpoint transport is explicit in `named_services.namespaces.<namespace>.providers`.
The list is plural because one namespace may be split across providers by
operation, ref, or object kind:

In that `providers` list, `operations` is the provider capability contract. In
`clients.<client_id>.tools`, `allowed_operations` controls the model-callable
tool surface for that client. `clients.canvas.resolver.enabled` lets the canvas
resolver call the provider for object refs; provider code decides which concrete
`object.action` values are accepted.

| `transport` | Runtime path | Use when |
| --- | --- | --- |
| `bundle_registry` | same KDCube runtime loads the owner bundle object and calls `named_services()` directly | the provider bundle is deployed in the same cube and the caller wants the fastest request-bound path |
| `bundle_operation` | same KDCube runtime calls the owner bundle's `@api(alias="named_service")` operation | the owner has only exposed the API facade or the integration wants the operation envelope |
| `module` | same Python runtime imports an explicit module/factory and calls the returned registry/provider | the provider lives in another module/package already importable in the current runtime |

`bundle_registry` and `module` preserve the request/auth context; they only
change how the provider object is reached. Provider code still owns object-level
permission checks.

## Client Surface

Callers use a named service client instead of hardcoding owner bundle routes.

```python
client = get_named_service_client()

result = await client.action(
    object_ref="task:issues/BUG-123",
    action="open",
    context={"source_surface": "sdk.canvas.pinboard"},
)
```

The client resolves by `object_ref` first. When no object ref exists yet, it
resolves by provider or namespace and operation:

```python
items = await client.search(
    provider="task.issue",
    namespace="task",
    query="blocked auth bugs",
    search_mode="hybrid",
    limit=20,
)
```

The client may choose a transport from provider capabilities and caller needs:

| Need | Preferred transport |
| --- | --- |
| same process/provider loaded locally | local |
| browser widget read or bounded write | API |
| external tool/client call | MCP |
| durable async mutation or command | Data Bus |

Data Bus responses mean that a command was accepted into the durable stream.
The domain result arrives through the handler result/reply path or by later
reading the object state.

## Auth Context

Auth belongs to the transport and runtime context. It is not part of model-call
arguments.

| Caller | Auth carrier | Provider receives |
| --- | --- | --- |
| KDCube widget/main UI over API | platform headers/cookies/session | resolved tenant, project, user, roles |
| same-origin browser MCP | MCP HTTP request auth, including cookies where the platform allows them | resolved tenant, project, user, roles |
| external MCP client | bearer/id token or bundle-issued/federated token | resolved tenant, project, user, roles |
| Data Bus browser client | authenticated Socket.IO/SSE peer or federated Data Bus token | message actor and tenant/project stream scope |
| server-side bundle call | current runtime context or explicit local context | current tenant, project, user, roles when present |
| scheduled job / cron | explicit bundle-job context | tenant, project, job principal, job metadata |
| scheduled job on behalf of a user | restored saved user auth context plus job metadata | original user principal, tenant, project, executing bundle id |

MCP tool schemas should not expose `cookie`, `authorization`, `user_id`, or
`roles` as ordinary tool parameters. The platform/adapter resolves those from
the request and passes a `NamedServiceContext` to the provider.

The SDK primitive is:

```python
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceClient,
    NamedServiceContext,
)

# API/MCP/Data Bus handlers that already have a bound runtime request:
client = NamedServiceClient.from_current_request(registry)

# Data Bus handler:
client = NamedServiceClient.from_data_bus_context(registry, ctx)

# Scheduled jobs and other headless bundle work:
client = NamedServiceClient.for_bundle_job(
    registry,
    tenant=tenant,
    project=project,
    bundle_id="task-tracker@1-0",
    job_alias="nightly-index",
)
```

Provider code reads `ctx.auth_context.principal_kind` when it needs to
distinguish a user request from job/system/service work. A bundle is the
execution/provider context (`bundle_id`), not the caller principal. Headless
contexts do not fake a user, and delegated jobs can preserve the saved user
principal while marking the call source as `bundle_job`.

### Scoped MCP Tokens

Some MCP servers are exposed by a bundle for an external client process such
as Claude Code. In that path there may be no live browser request and no
current platform user session. The task-and-memo email integration uses this
pattern:

1. The bundle prepares a run document.
2. The bundle signs a short-lived, run-scoped MCP token.
3. The public `@mcp(...)` route validates that token.
4. The MCP tools operate only inside the scoped run.

Named service MCP adapters should follow the same shape. The generic SDK
primitive is a signed `AuthContext` token:

```python
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import (
    AuthContext,
    sign_auth_context_token,
    verify_auth_context_token,
)

saved_user_context = AuthContext.from_mapping(saved_user_auth_doc)
auth = AuthContext.for_bundle_job(
    tenant=tenant,
    project=project,
    bundle_id="task-and-memo-app@1-0",
    job_alias="email-check",
    on_behalf_of=saved_user_context,
)

token = sign_auth_context_token(
    auth,
    secret=secret,
    audience="task-and-memo-app@1-0:mcp/named-services",
    ttl_seconds=900,
    metadata={"run_id": run_id},
)

# Inside the public MCP route after reading the configured header:
auth = verify_auth_context_token(
    token,
    secret=secret,
    audience="task-and-memo-app@1-0:mcp/named-services",
)
client = NamedServiceClient(registry, auth_context=auth, transport="mcp")
```

The token is not a replacement for platform auth. It is the bundle/provider
credential for a bounded external client run. Use a narrow audience, short TTL,
and provider-owned run metadata.

## Transport Adapter Contract

Each transport adapter maps the same semantic operation to its native protocol.

```text
API:
  POST /api/.../named-services/{provider}/{operation}

MCP:
  named_service.task.issue.object.search(...)
  named_service.task.issue.object.action(...)

Data Bus:
  subject: named_service.task.issue.object.upsert
  object_ref: task:issues/BUG-123
  payload: named service request

local:
  await provider.object_search(ctx, request)
```

Exact URLs and MCP tool names are implementation details. The operation names,
context rules, idempotency fields, and response semantics are the stable
contract.

Transport adapters are context adapters plus protocol adapters:

- API/MCP adapters hydrate `AuthContext` from the already-authenticated request
  and call the provider/client surface.
- Public MCP adapters used by external clients can verify a scoped signed
  `AuthContext` token and then call the same provider/client surface.
- Data Bus adapters hydrate `AuthContext` from message actor metadata and
  tenant/project stream scope.
- Local callers pass an explicit context or use the currently bound runtime
  context.

The provider surface remains callable without entering platform ingress when
the caller is already running inside trusted bundle/platform code.

### API Local Loop

The API adapter is the first concrete transport adapter. A bundle mounts a
normal `@api(...)` operation, and that operation dispatches through the local
named-service registry:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceRegistry,
    dispatch_named_service_api_request,
)

class MyEntrypoint(...):
    def _named_service_registry(self) -> NamedServiceRegistry:
        registry = NamedServiceRegistry()
        registry.register(self.task_issue_provider())
        return registry

    @api(method="POST", alias="named_service", route="operations")
    async def named_service_api(self, **payload):
        return await dispatch_named_service_api_request(
            self._named_service_registry(),
            payload,
        )
```

The platform API route authenticates the browser/widget request and binds the
request context. The helper then creates a `NamedServiceClient` with
`transport="api"` and calls the provider in-process. It must not call back into
`/api/integrations/...`; that would add latency, duplicate auth handling, and
break scheduled/local callers.

### Request-Bound Runtime-Local Bridges

A composition bundle may need to resolve an object owned by another bundle
while handling a current browser/widget request. Same-KDCube namespace-service
clients should use the configured endpoint transport:

- `bundle_registry` loads the owner bundle object and calls `named_services()`
  directly. Singleton owner bundles are served from the loader singleton cache
  after the first load.
- `bundle_operation` calls the owner bundle's `@api(alias="named_service")`
  facade and is the compatibility/fallback path.

The lower-level operation bridge remains available for explicit bounded
operation calls:

```python
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation

raw = await call_bundle_operation(
    bundle_id="task-tracker@1-0",
    operation="named_service",
    data={
        "operation": "object.action",
        "provider": "task.issue",
        "namespace": "task",
        "object_ref": "task:issues/BUG-123",
        "action": "open",
    },
)
```

Platform runtime binds the same caller context while executing request-scoped
bundle code. Peer calls stay inside the same KDCube process and reuse the
current tenant/project/session visibility checks. Bundles do not replay browser
cookies, mint ad-hoc tokens, or POST back to their own public API.

This bridge is for request-scoped bounded operations. Headless jobs should use
an explicit `AuthContext` and provider/client path instead of assuming a live
browser session exists.

### Configured Canvas/Chat Resolver

Composition bundles can configure namespace resolvers for canvas pins and chat
context chips. The chat widget already routes object actions through the
bundle's `canvas_object_action` operation, so the same resolver registry covers
both surfaces.

```yaml
named_services:
  namespaces:
    task: {}
```

In the bundle entrypoint:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    register_configured_named_service_canvas_resolvers,
)

def _canvas_object_resolvers(self, payload, *, user_id):
    registry = build_default_canvas_resolver_registry(store)
    register_configured_named_service_canvas_resolvers(
        registry,
        namespaces=self.bundle_prop("named_services.namespaces", {}) or {},
        tenant=tenant,
        project=project,
        logger=_log,
    )
    return registry
```

The helper registers `NamedServiceCanvasObjectResolver` instances. A `task:`
card then resolves by calling the owning bundle's `named_service` operation
through the request-bound bridge, preserving the user's current auth/session.

## Implementation Order

1. Define SDK types and provider/client interfaces.
2. Add local provider registry and local client dispatch.
3. Add API adapter for widgets and scene hosts.
4. Add MCP adapter for external tools/clients with request-level auth
   context.
5. Add Data Bus adapter for durable async commands.
6. Migrate existing resolver actions such as `canvas_object_action` to delegate
   to named service provider `object.action`.
7. Add provider declarations for task, memory, canvas, ReAct artifacts, and
   knowledge as each owner is ready.

Existing bundle-specific operations can stay as compatibility routes while
they delegate to the named service provider.

## Current SDK Package

The initial SDK package is:

```text
kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers
```

It currently provides:

- async auth context, request, response, operation, and provider spec types;
- `AuthContext` for request, Data Bus, job, service, system, and local callers;
- `@named_service_provider(...)` metadata decorator;
- `NamedServiceProvider` async base class;
- in-process `NamedServiceRegistry`;
- async `NamedServiceClient` local dispatch;
- API transport helper for local-loop `@api(...)` dispatch;
- request-bound local bundle operation bridge for peer bundle API calls;
- API endpoint client for named-service calls through that bridge;
- canvas/chat object resolver adapter plus reusable config registration helper;
- client-scoped named-service tool adapter that reads
  `named_services.namespaces.<namespace>.clients.<client_id>.tools`;
- client constructors for current request, Data Bus context, and bundle-job
  context.

MCP and Data Bus platform adapter routes are still separate integration work.
Their transport names are part of provider capabilities so bundle code can
declare the intended exposure before each adapter is mounted.

## Design Checklist

When introducing a named service provider:

- choose one owner;
- define provider id, labels, and purpose;
- define canonical ref grammar and object kinds when the provider owns refs;
- define `provider.about` and `provider.capabilities`;
- define `object.schema` for each object kind that agents may create, update,
  delete, render, or pull from;
- define object operations and action names;
- define `rehost` for large attachment refs that must become `fi:` artifacts;
- define `block.produce` / `block.render` when ReAct should project
  provider-owned objects as model-visible blocks;
- define pagination, search mode, revision, and idempotency rules;
- define relation operations if the provider connects multiple objects;
- define auth and visibility policy;
- choose transport adapters per operation;
- keep Data Bus for durable async commands and stream-backed mutations;
- return refs or hosted files for large object bodies or attachments;
- document how scene `target_surface` commands are produced;
- add tests for provider validation, client dispatch, auth context, and
  transport-specific request shapes.
