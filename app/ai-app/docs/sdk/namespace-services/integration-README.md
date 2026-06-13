---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
title: "Namespace Services: Integration Flow"
summary: "Visual host/client integration flow for namespace service providers, using task-tracker and versatile as the current reference path."
status: design
tags: ["sdk", "namespace-services", "integration", "task-tracker", "versatile", "scene", "canvas", "chat"]
updated_at: 2026-06-12
keywords:
  [
    "namespace service integration",
    "provider host",
    "client bundle",
    "task tracker provider",
    "versatile client",
    "object action",
    "canvas object resolver",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
---
# Namespace Services: Integration Flow

This is the current reference shape for connecting one bundle that owns a
namespace to another bundle that wants to display, search, open, or otherwise
act on that namespace.

## Notation

Diagrams use `Party.field` when a value is owned or emitted by a specific
party. For example:

- `Canvas.card.object_ref` is the ref stored on the canvas/card event.
- `ReAct.target.logical_path` is the logical path carried into ReAct block
  production.
- `Discovery.entry` is a provider record from Named Service Discovery.
- `TaskProvider.ret.extra.event_source_id` is returned by the provider's
  resolver function.

If a step uses data, the diagram names the owner of that data. No host layer
derives task object kinds or task rendering rules from URI shape. The host may
split `task:...` into namespace `task` only to find the namespace resolver;
the provider function owns the rest of the interpretation.

## System Picture

```text
Provider host bundle                                      Consumer/composition bundle
task-tracker@1-0                                          versatile@2026-03-31-13-36

TaskProvider data and functions                           Consumer config and surfaces
  TaskProvider.namespace = task                             Versatile.props.surfaces.as_consumer
  TaskProvider.provider_id = task.issue                       agents.main.tools.kind = named_service
  TaskProvider.refs = task:issue:*                           agents.main.event_sources.kind = named_service
  TaskProvider.object_kinds = task.issue/task.attachment       ui.canvas.resolvers.kind = named_service
  TaskProvider.named_services() registry
  TaskProvider.event_resolve(request.object_ref)
  TaskProvider.object_get / object_action / block_produce

Provider startup                                          Consumer startup
  TaskProvider.entrypoint.on_bundle_load                    Versatile.entrypoint initializes:
    -> Discovery.register(TaskProvider.spec)                   canvas resolver adapter
    -> Discovery.entry:                                        artifact rehoster
         tenant/project                                       event-source resolver bridge
         bundle_id=task-tracker@1-0                           model tool specs
         provider_id=task.issue
         operations/ref/object_kinds

Runtime call bridge
  Consumer request + AuthContext
        |
        v
  Named Service Discovery selects Discovery.entry by operation/ref/namespace
        |
        v
  bundle_registry transport calls TaskProvider.named_services() in-process
  or bundle_operation transport calls TaskProvider.@api(alias="named_service")
```

## Startup And Discovery

```text
TaskProvider bundle load
  TaskProvider.spec:
    spec.namespace = task
    spec.provider_id = task.issue
    spec.refs = task:issue:*, task:issue:attachment:*/attachments/*, task:issue:*
    spec.object_kinds = task.issue, task.attachment
    spec.operations includes event.resolve, object.get, block.produce, ...

        |
        v

TaskProvider.entrypoint.on_bundle_load
  reads TaskProvider.named_services().providers()
  writes Discovery.entry into Redis:
    Discovery.entry.scope = Request.tenant/project
    Discovery.entry.bundle_id = task-tracker@1-0
    Discovery.entry.provider_id = task.issue
    Discovery.entry.operations = TaskProvider.spec.operations
    Discovery.entry.refs = TaskProvider.spec.refs
    Discovery.entry.object_kinds = TaskProvider.spec.object_kinds

        |
        v

Consumer bundle startup
  reads Versatile.props.surfaces.as_consumer
  registers local adapters:
    CanvasResolver(namespace=task) from ui.canvas.resolvers
    ArtifactRehoster(namespace=task) from agent event_sources pull policy
    EventSource(named_services.task) from agent event_sources block policy
    EventSourceResolver(namespace=task) from agent event_sources discovery

These adapters do not own task semantics. They only know:
  Consumer.config.namespace = task
  Consumer.config.allowed surfaces/operations
  Discovery can find the provider when a request happens
```

## Object Action Flow

Opening a task card from canvas or chat:

```text
User action
  User.clicks(Canvas.card)
  Canvas.card.object_ref = task:issue:issue_123
  Canvas.action = open

        |
        v

Consumer widget/backend
  Versatile.canvas_object_action receives:
    Request.auth = current user/session/tenant/project
    Canvas.card.object_ref
    Canvas.action

        |
        v

Consumer resolver adapter
  CanvasResolver sees namespace(task) from Canvas.card.object_ref
  Builds NamedServiceRequest:
    request.operation = object.action
    request.namespace = task
    request.object_ref = Canvas.card.object_ref
    request.action = Canvas.action
    request.context.auth = Request.auth

        |
        v

Discovery and transport
  Discovery.resolve(request.operation, request.namespace, request.object_ref)
    -> Discovery.entry(provider_id=task.issue, bundle_id=task-tracker@1-0)
  Transport uses Discovery.entry.endpoint:
    bundle_registry -> TaskProvider.named_services()
    bundle_operation -> TaskProvider.@api(alias="named_service")

        |
        v

Provider operation
  TaskProvider.object_action(ctx=Request.auth, request)
  TaskProvider owns:
    TaskProvider.issue data
    TaskProvider.permission rules
    TaskProvider.target surface names
  Returns:
    TaskProvider.ret.object = compact task descriptor
    TaskProvider.ret.ui_event.target_surface = task_tracker.issue_editor
    TaskProvider.ret.attrs.object_ref = request.object_ref

        |
        v

Consumer scene
  Versatile.scene reads TaskProvider.ret.ui_event.target_surface
  Versatile.scene opens task-tracker editor iframe
```

The task ref remains `task:issue:issue_123` the entire time. Canvas owns card
layout. Task-tracker owns task semantics.

## ReAct External Event Block Flow

When a task card is dropped into chat, the event occurrence may be authored by
canvas or a widget. That occurrence source is not rewritten to `named_services`.

```text
Ingress/event lane
  Event.payload.event.event_source_id = Canvas.event_source_id
    example: task.context or task_tracker.issue
  Event.payload.event.logical_path = task:issue:issue_123
  Event.payload.event.hosted_uri = task:issue:issue_123
  Event.payload.event.title = Canvas.card.title

        |
        v

ReAct timeline browser
  ReAct.target.event_source_id = Event.payload.event.event_source_id
  ReAct.target.logical_path = Event.payload.event.logical_path
  ReAct.target.hosted_uri = Event.payload.event.hosted_uri

Step 1: authored source gets first chance
  EventSourceSubsystem.by_event_source_id(ReAct.target.event_source_id)
  If that source has block_production policy:
    apply it

Step 2: if no blocks were produced, route by owner URI
  EventSourceSubsystem.resolve_event_source_for_ref(ReAct.target.logical_path)
    namespace = split_namespace(ReAct.target.logical_path) = task
    resolver = registered resolver function for namespace task

        |
        v

Named-service resolver bridge
  Builds NamedServiceRequest:
    request.operation = event.resolve
    request.namespace = task
    request.object_ref = ReAct.target.logical_path
    request.context.source = event_source_resolver
  Discovery.resolve(event.resolve, task, request.object_ref)
    -> Discovery.entry(provider_id=task.issue)
  Calls TaskProvider.event_resolve(ctx=Request.auth, request)

        |
        v

Provider resolver function
  TaskProvider.resolve_task_event_source(request.object_ref)
  Returns:
    TaskProvider.ret.extra.event_source_id = named_services.task
    TaskProvider.ret.extra.object_ref = request.object_ref
    TaskProvider.ret.extra.object_kind = task.issue OR task.attachment
    TaskProvider.ret.extra.namespace = task

        |
        v

ReAct block production through resolved source
  EventSourceSubsystem applies source named_services.task
  Named-service block policy builds:
    request.operation = block.produce
    request.namespace = task
    request.object_ref = TaskProvider.ret.extra.object_ref
  Discovery selects TaskProvider again for block.produce
  TaskProvider.block_produce returns:
    TaskProvider.ret.extra.blocks[] = model-visible blocks
```

The host only performs namespace dispatch. It does not infer `task.issue`,
`task.attachment`, markdown shape, or editor target from the URI. Those values
come from `TaskProvider.event_resolve` and `TaskProvider.block_produce`.

## Pull Flow

Materializing a task-owned object or attachment into the ReAct workspace:

```text
Agent/tool
  ReAct calls react.pull(paths=[
    "task:issue:issue_123",
    "task:issue:attachment:issue_123/attachments/ta_1/v000001/evidence.md"
  ])

        |
        v

Consumer artifact rehoster
  EventSourceSubsystem.namespace_rehoster(task)
  Rehoster builds NamedServiceRequest per path:
    request.operation = object.get
    request.namespace = task
    request.object_ref = ReAct.pull.path
    request.response_mode = stream
    request.context.source = react.pull
    request.context.auth = Request.auth

        |
        v

Discovery and provider
  Discovery.resolve(object.get, task, request.object_ref)
    -> Discovery.entry(provider_id=task.issue)
  TaskProvider.object_get(ctx=Request.auth, request)

        |
        v

Provider stream result
  For task issue JSON:
    TaskProvider.response.ret.object = task issue descriptor
    TaskProvider.chunks = UTF-8 JSON bytes
    TaskProvider.media_type = application/vnd.kdcube.task.issue+json

  For task attachment:
    TaskProvider.response.ret.object = task attachment descriptor
    TaskProvider.chunks = attachment bytes
    TaskProvider.media_type = attachment MIME

        |
        v

ReAct workspace materialization
  Runtime writes TaskProvider.chunks to:
    ReAct.fi.logical_path = fi:turn_<id>....
    ReAct.physical_path = current turn workspace file
  react.pull returns:
    PullResult.materialized[].logical_path = ReAct.fi.logical_path
    PullResult.materialized[].response = TaskProvider.response
    PullResult.errors[] = TaskProvider.error response, if any
```

The provider enforces access before streaming. If access is denied, missing, or
misconfigured, `react.pull` returns an `errors` row containing the provider's
named-service error code and response.

Normal block rendering does not copy provider bytes. ReAct gets bytes from
foreign namespaces only through explicit pull materialization.

## Host-File Flow

Hosting a ReAct/runtime file into a provider namespace is the reverse direction
from pull. Pull reads `Provider.object_ref` into `ReAct.fi`. Host-file gives
the provider a caller-owned file descriptor and receives `Provider.object_ref`
for the hosted file.

```text
ReAct/runtime
  ReAct.file_ref = fi:turn_1.files/report.md
  ReAct.filename = report.md
  ReAct.mime = text/markdown

        |
        v

Agent tool call
  named_services.host_file(
    namespace = task,
    object_ref = task:issue:issue_123,
    file_ref = ReAct.file_ref,
    filename = ReAct.filename,
    mime = ReAct.mime
  )

        |
        v

Named-service tool wrapper
  Builds NamedServiceRequest:
    request.operation = object.host_file
    request.namespace = task
    request.object_ref = task:issue:issue_123
    request.payload.file.ref = ReAct.file_ref
    request.payload.file.filename = ReAct.filename
    request.payload.file.mime = ReAct.mime
    request.context.auth = Request.auth

        |
        v

Discovery and provider
  Discovery.resolve(object.host_file, task, request.object_ref)
    -> Discovery.entry(provider_id=task.issue)
  TaskProvider.object_host_file(ctx=Request.auth, request)

        |
        v

Provider storage and response
  TaskProvider reads or resolves ReAct.file_ref under Request.auth
  TaskProvider writes bytes into TaskProvider.attachment_store
  TaskProvider returns:
    TaskProvider.ret.attrs.object_ref =
      task:issue:attachment:issue_123/attachments/ta_1/v000001/report.md
    TaskProvider.ret.object.identity.object_kind = task.attachment
    TaskProvider.ret.extra.attach_with = provider-specific upsert hint
```

Hosting does not mutate the parent object by itself. If the agent wants the
file cited on the issue, it must make the provider-declared object mutation:

```text
Agent tool call
  named_services.upsert_object(
    namespace = task,
    object_ref = task:issue:issue_123,
    object_json = {
      attachment_refs: [{
        ref: TaskProvider.ret.attrs.object_ref,
        filename: report.md,
        mime: text/markdown
      }]
    }
  )

        |
        v

TaskProvider.object_upsert
  verifies Request.auth can edit/attach
  cites Provider.attachment_ref on Provider.issue
```

The bytes do not travel in JSON. The initial implementation uses artifact refs
or trusted runtime-local file descriptors; future transports can replace that
descriptor handoff with request-body streaming without changing the provider
operation name or the two-step host/cite strategy.

## Provider Host Checklist

1. Define a provider class using `@named_service_provider(...)`.
2. Register that provider in a `NamedServiceRegistry`.
3. Expose `named_services()` so same-KDCube clients can call the registry
   directly.
4. Register the provider registry into Named Service Discovery during
   `on_bundle_load` after required local storage/indexes are ready.
5. Expose one bounded API operation, normally `@api(alias="named_service")`,
   when `bundle_operation` or external clients need an API facade.
6. Dispatch the operation with `dispatch_named_service_api_request(...)`; the
   same helper handles `response_mode: stream`.
7. Return canonical object descriptors under `ret.object`, list/search results
   under `ret.items`, common response metadata under `ret.attrs`, UI commands
   under `ret.ui_event`, and bounded provider-specific details under
   `ret.extra`.
8. Implement `object.schema` for each object kind that agents may mutate.
9. Implement `event.resolve` as the lightweight provider function for
   `uri -> resolution info` such as `event_source_id`, `object_ref`, and
   `object_kind`. It must not read the object body.
10. Implement streamed `object.get` for attachment refs that should become
   ReAct `fi:` artifacts.
11. Implement `object.host_file` when callers need to create provider-owned
   file refs from runtime files or artifact refs.
12. Implement `block.produce` / `block.render` when provider objects should
   become model-visible blocks.
13. Keep owner storage and mutation rules inside the provider bundle.

Current task-tracker reference points:

```text
applications/playground/bundles/task-tracker@1-0/issues/named_service.py
applications/playground/bundles/task-tracker@1-0/entrypoint.py
applications/playground/bundles/task-tracker@1-0/tests/test_named_service_provider.py
```

## Client Bundle Checklist

1. Configure `surfaces.as_consumer.agents.<agent>.tools` for model-visible
   named-service tools and namespace allow-lists.
2. Configure `surfaces.as_consumer.agents.<agent>.event_sources` for
   provider-backed block production and `react.pull` materialization.
3. Configure `surfaces.as_consumer.ui.canvas.resolvers` when canvas/chat object
   actions should resolve refs in the namespace.
4. Declare `kind: named_service` under
   `surfaces.as_consumer.agents.<agent>.tools`.
5. Register artifact rehosters with
   `register_configured_named_service_artifact_rehosters(...)`.
6. Register namespace event sources with
   `register_configured_named_service_event_sources(...)` when lane events can
   carry `event_source_id: named_services.<namespace>`.
7. Route object open results through the scene surface registry.

Current versatile reference points:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/config/bundles.template.yaml
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py
```

## Resolution Scope

The current resolution path is generic for configured namespaces:

```text
object_ref -> namespace -> Named Service Discovery -> provider endpoint -> provider operation
```

Named Service Discovery is a Redis-backed tenant/project provider table. It is
not a one-namespace/one-bundle map: multiple bundles may register providers for
the same namespace when they expose different operations, refs, or object
kinds. The runtime selects a provider per request.

For model clients, `provider.about` explains the service and base objects.
`object.schema` explains concrete object payloads such as `task.issue` or
`task.attachment`. Generic CRUD tools stay generic; the provider supplies the
entity shape.

For canvas/chat resolution, the client only enables the namespace resolver.
The provider remains the authority for concrete resolver actions and returns a
normal named-service response or rejection for each request.

## Canvas Proxy Resolve And Action Flow

Canvas stores a proxy card with `Canvas.card.object_ref`. The card does not own
the external object actions. The namespace provider owns the URI grammar,
object-kind split, capabilities, auth checks, and action result.

```text
User opens card drawer
  -> Canvas.card.object_ref = task:issue:attachment:<issue_id>/attachments/<attachment_id>/v<version>/<filename>
  -> Canvas server calls Provider.object.resolve(object_ref=Canvas.card.object_ref)
  -> Provider resolves Provider.ref:
       Provider.ref.object_kind = task.attachment
       Provider.ref.parent = task:issue:<issue_id>
       Provider.ref.capabilities = {preview, open, download, ...}
  -> Canvas stores the resolver result on the card UI state only

User clicks card action
  -> Canvas server calls Provider.object.action(action=<clicked>, object_ref=Canvas.card.object_ref)
  -> Provider parses Provider.ref again
  -> Provider enforces Provider.auth policy
  -> Provider returns one bounded result:
       preview: Provider.object metadata/short text
       open: Provider.ui_event target surface command
       download: Provider.bytes encoded for browser download or an explicit provider download handle
       error: Provider.error with status/message
```

`object.resolve` is the lightweight `uri -> resolution info` contract for
canvas/chat affordance discovery. It must not read a large object body or stream
bytes. `object.action` is used after the user chooses a concrete action such as
`preview`, `open`, or `download`.

For example, a task issue and a task attachment share the `task` namespace but
resolve differently:

```text
task:issue:<issue_id>
  Provider.object_kind = task.issue
  Provider.actions = preview, open
  Provider.download = false

task:issue:attachment:<issue_id>/attachments/<attachment_id>/v<version>/<filename>
  Provider.object_kind = task.attachment
  Provider.parent = task:issue:<issue_id>
  Provider.actions = preview, open, download
  Provider.download = true when Provider.auth allows read
```

No canvas code should special-case `task`, `memo`, `file`, or any other
namespace-owned object kind. Canvas chooses visible buttons from the resolver
capabilities returned by the owner provider.
