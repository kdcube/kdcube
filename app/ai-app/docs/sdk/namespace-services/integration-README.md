---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
title: "Namespace Services: Integration Flow"
summary: "Visual host/client integration flow for namespace service providers, using task-tracker and versatile as the current reference path."
status: design
tags: ["sdk", "namespace-services", "integration", "task-tracker", "versatile", "scene", "canvas", "chat"]
updated_at: 2026-06-17
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
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
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
    spec.search_scopes = provider-declared searchable scoped namespaces
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
    Discovery.entry.search_scopes = TaskProvider.spec.search_scopes

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

## Search Scope Discovery And Tool Catalog Flow

Search scopes are provider-declared object spaces. They are carried by provider
registration/discovery so a consumer can render them in the model tool catalog
without first calling the provider. `provider.about` remains available for
richer domain guidance, but it is not required for the fast "which namespace
argument should I use for search?" path.

```text
1. Provider app startup
   executor: provider bundle entrypoint / named_service_provider spec
   surface: provider registration surface
   customized: yes, provider owns object-space names
   emits:
     Provider.spec.namespace = sensor
     Provider.spec.search_scopes:
       - sensor:temperature     label="temperature readings"
       - sensor:humidity:aggr   label="humidity aggregates"

        |
        v

2. Named Service Discovery
   executor: RedisNamedServiceDiscovery.register(...)
   surface: tenant/project discovery table
   customized: no, generic SDK persistence
   stores:
     Discovery.entry.spec.search_scopes = Provider.spec.search_scopes

        |
        v

3. Consumer ReAct tool catalog refresh
   executor: ToolSubsystem.react_tools(...)
   surface: consumer app ReAct decision setup
   customized: no namespace semantics
   work:
     read configured base namespaces from surfaces.as_consumer.agents.<agent>.tools
     read provider Discovery.entry rows once for those namespaces
     bind named_service_discovery_entries into the generic named_services tool module

        |
        v

4. Generic named-service tool catalog
   executor: named_services_providers.tools.list_tools()
   surface: model-callable tool metadata
   customized: consumer allow-list only
   work:
     if object.search is allowed for base namespace sensor:
       expose named_services.search_objects
       attach search_scopes_by_namespace.sensor from discovery/config

        |
        v

5. ReAct prompt renderer
   executor: build_tools_block(...)
   surface: rendered tool catalog visible to the model
   customized: no provider code
   renders:
     Scope:
       - namespaces applicable: sensor
       - provider search scopes:
           sensor:
             - sensor:temperature - temperature readings
             - sensor:humidity:aggr - humidity aggregates

        |
        v

6. Model tool call
   executor: LLM generation governed by ReAct harness
   surface: action/tool lane
   customized: model behavior only
   emits:
     named_services.search_objects(
       namespace="sensor:temperature",
       query="lab reading spike",
       limit=10
     )

        |
        v

7. Provider object.search
   executor: provider NamedServiceProvider.object_search(...)
   surface: provider app backend
   customized: yes, provider owns search/index semantics
   receives:
     NamedServiceRequest.namespace = sensor:temperature
   returns:
     bounded object descriptors with canonical refs, labels, summaries,
     object_kind, mime, and provider cursor when applicable

        |
        v

8. Search result side channel
   executor: named_services.search_objects wrapper
   surface: ReAct tool result + subsystem artifact
   customized: no provider semantics
   emits:
     named_service.search_results
     payload.items[] = context-compatible handles

        |
        +--> Capable UI client
             surface: optional named-service search results widget
             work:
               render rows
               click -> normal object.action resolver path
               drag  -> normal context attach/pin path
```

> The "normal context attach/pin path" means each result row is dragged as a
> [context pin](../npm/components-core/context-pin-contract-README.md): one envelope
> with the native uri in `ref`, so it opens/pins natively rather than as a generic object.

The base namespace in `namespaces applicable` remains the policy boundary. The
provider search scopes beneath it are valid `namespace` arguments for
`named_services.search_objects`; each one means "search this provider-declared
object space."

## Object Action Flow

Example: the user presses **Download** on a canvas card whose ref is a task
attachment. Each step names the code executor, the surface inside that
executor, and whether that step is generic or app-specific.

In this section:

- **consumer app** means the app that mounted the canvas/chat/scene surface and
  configured `surfaces.as_consumer`; in the current reference this is
  `versatile@2026-03-31-13-36`;
- **consumer app operation** means an ordinary bundle/app `@api` method, for
  example `@api(alias="canvas_object_action", route="operations")`;
- **provider app** means the app that owns the namespace and registered
  `named_services()` for it; in the current reference this is
  `task-tracker@1-0`.

```text
1. Browser
   executor: CanvasBoard React component mounted in the consumer app UI
   surface: canvas/pinboard UI surface
   customized: no, generic canvas component
   input:
     Canvas.card.object_ref =
       task:issue:attachment:issue_1/attachments/ta_1/v000001/evidence.md
     action = download

        |
        v

2. Browser embedding adapter
   executor: the consumer app/widget/scene that embedded CanvasBoard
   surface: browser-to-app operation adapter
   customized: yes, per consumer app
   call:
     POST ConsumerApp.@api(alias="canvas_object_action", route="operations")
     payload.object_ref = Canvas.card.object_ref
     payload.action = download

   Current reference:
     ConsumerApp = versatile@2026-03-31-13-36
     operation = VersatileEntrypoint.canvas_object_action(...)

        |
        v

3. Consumer app backend
   executor: consumer bundle entrypoint method decorated with @api
   surface: operations API surface of the consumer app
   customized: yes, consumer chooses which resolvers are registered
   work:
     Request.auth = current user/session/tenant/project
     build CanvasObjectResolverRegistry
     register configured resolvers from Consumer.config.surfaces.as_consumer.ui.canvas
     call registry.object_action(payload)

        |
        v

4. Generic SDK registry
   executor: CanvasObjectResolverRegistry
   surface: canvas object resolver dispatch
   customized: no, generic SDK
   work:
     namespace = split_namespace(Canvas.card.object_ref) = task
     resolver = registered resolver for namespace task

        |
        v

5. Generic named-service resolver bridge
   executor: NamedServiceCanvasObjectResolver
   surface: configured canvas/chat resolver adapter
   customized: configured, not hardcoded
   builds:
     NamedServiceRequest.operation = object.action
     NamedServiceRequest.namespace = task
     NamedServiceRequest.object_ref = Canvas.card.object_ref
     NamedServiceRequest.action = download
     NamedServiceRequest.context.source = canvas.object_action
     NamedServiceRequest.context.tenant/project = Consumer request scope

        |
        v

6. Generic provider discovery/transport
   executor: named-service endpoint/discovery transport
   surface: service discovery + bundle_registry or bundle_operation transport
   customized: provider endpoint config/discovery entry
   work:
     Discovery.resolve(object.action, task, object_ref)
       -> Discovery.entry(provider_id=task.issue, bundle_id=task-tracker@1-0)
     bundle_registry transport:
       calls TaskTrackerEntrypoint.named_services().provider("task.issue")
     bundle_operation transport:
       calls TaskTrackerEntrypoint.@api(alias="named_service")

        |
        v

7. Provider app backend
   executor: TaskIssueNamedServiceProvider.object_action(...)
   surface: provider registry surface inside task-tracker app
   customized: yes, provider owns task semantics
   work:
     parse object_ref
     classify object_kind = task.attachment
     enforce auth/capability
     run provider action = download
     build provider-owned download URL with bundle_operation_url(...)
   returns:
     ret.extra.download_url =
       /api/integrations/bundles/.../operations/issue_attachment_download?object_ref=...
     ret.extra.filename = evidence.md
     ret.extra.mime = text/markdown

        |
        v

8. Browser
   executor: CanvasBoard React component
   surface: generic canvas download behavior
   customized: no
   work:
     create temporary <a href=download_url download=filename>
     browser GETs download_url with its current cookies/session

        |
        v

9. Provider app backend binary operation
   executor: TaskTrackerEntrypoint.issue_attachment_download(...)
   surface: provider app @api(alias="issue_attachment_download", route="operations")
   customized: yes, provider owns bytes/storage
   work:
     authenticate request again
     parse object_ref or issue_id/attachment_id
     read provider storage
     return BundleBinaryResponse(bytes, filename, mime)

        |
        v

10. Browser
    executor: browser download stack
    surface: native file download
    customized: no
    result:
      user receives evidence.md
```

The task attachment ref remains provider-owned the entire time. Canvas owns the
card and click UI. The consumer app owns the `@api` entrypoint that its canvas
calls. The task provider owns task/attachment semantics and byte access.

## Same Ref Across Surfaces

The same provider-owned ref can travel through every consumer surface. The
consumer changes; the provider contract does not.

```text
Provider object
  TaskProvider.object_ref =
    task:issue:attachment:issue_1/attachments/ta_1/v000001/evidence.md

        |
        +--> chat context chip
        |     Consumer calls object.resolve for label/actions.
        |     On click, Consumer runs default_open_effect_action from provider.
        |
        +--> canvas/pinboard card
        |     Consumer stores Canvas.card.object_ref unchanged.
        |     On open/download, Consumer calls object.action.
        |
        +--> scene host
        |     Consumer receives Provider.ret.ui_event.target_surface.
        |     Scene routes that target to the mounted widget/component.
        |
        +--> ReAct context/materialization
              Consumer calls event.resolve + block.produce for model context.
              react.pull calls object.get(response_mode=stream) for bytes.
```

For an attachment ref, the task provider can return:

```text
object.resolve:
  object_kind = task.attachment
  actions = [preview, open, download]
  default_open_effect_action = download
  parent.object_ref = task:issue:issue_1

object.action(action=download):
  download_url = /api/integrations/bundles/.../issue_attachment_download?object_ref=...
  filename = evidence.md
  mime = text/markdown

object.get(response_mode=stream):
  chunks = attachment bytes for react.pull or another materializer
```

No consumer surface should hardcode that `task:` attachments download while
`task:` issues open an editor. The task provider declares that per concrete
object handle.

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
    PullResult.materialized[].object_ref = provider-returned canonical URI
    PullResult.materialized[].response = TaskProvider.response
    PullResult.errors[] = TaskProvider.error response, if any

  react.read(ReAct.fi.logical_path) emits:
    block.path = ReAct.fi.logical_path
    block.meta.object_ref = provider-returned canonical URI
    block.meta.source_namespace = provider root namespace
```

Owner projection then runs before the generic read block is committed:

```text
1. ReAct read tool
   executor: react.tools.read.handle_react_read
   surface: consumer app ReAct runtime
   customized: no provider semantics
   reads:
     block.meta.object_ref = task:... / mem:... / other provider ref

        |
        v

2. Owner event-source resolution
   executor: EventSourceSubsystem
   surface: consumer app event-source registry
   customized: provider may implement event.resolve
   work:
     try provider event.resolve(object_ref)
     else use registered named_services.<root_namespace> event source
   traces:
     react.read.owner_projection status=namespace_event_source/no_event_source/...

        |
        v

3. Named-service block policy
   executor: named_services.block_production.provider
   surface: generic named-service event-source adapter
   customized: no provider semantics
   work:
     call provider block.produce(object_ref=object_ref, target=read target)

        |
        v

4. Provider block production
   executor: provider NamedServiceProvider.block_produce(...)
   surface: provider app backend
   customized: yes, provider owns model-visible text shape
   returns:
     ret.extra.blocks[] with owner-authored ReAct blocks

        |
        v

5. ReAct visible context
   executor: react.read
   surface: current turn timeline
   work:
     append provider blocks when returned
     otherwise append generic textual fi: read block

        |
        v

6. Optional provider render projection
   executor: Timeline.render() named-service render adapter
   surface: consumer ReAct prompt rendering
   customized: no provider semantics in the adapter
   work:
     scan visible blocks for object_ref values
     group candidate blocks by named_services.<namespace> event source
     call provider block.render concurrently for relevant providers
     pass a bounded block snapshot plus render_context
     merge valid patches for provider-owned block indexes

        |
        v

7. Provider block rendering
   executor: provider NamedServiceProvider.block_render(...)
   surface: provider app backend
   customized: yes, provider owns final model-facing rendering
   receives:
     blocks[] with stable index values
     render_context.phase = timeline_projection
     render_context.audience = model
     render_context.event_source_id = named_services.<namespace>
   returns:
     patch operations such as patch_block, replace_block, append_block_after
```

The provider enforces access before streaming. If access is denied, missing, or
misconfigured, `react.pull` returns an `errors` row containing the provider's
named-service error code and response.

Normal block rendering does not copy provider bytes. ReAct gets bytes from
foreign namespaces only through explicit pull materialization. Provider
`block.render` receives already-produced visible blocks and returns rendering
patches; it is not a file/body materialization path.

The `fi:` path is only the local workspace location. Namespace-aware rendering
and policy selection uses the preserved `object_ref` / `source_namespace`
metadata, not the `fi:` prefix. The detailed runtime-boundary diagram for this
path is [ReAct Object Materialization](react-object-materialization-README.md).

## ReAct Named-Service Tool Flow

Example: the model calls `named_services.search_objects`,
`named_services.host_file`, or `named_services.upsert_object`. Each step names
where code executes, which surface executes it, and which part is customized.

```text
1. LLM generation
   executor: model stream governed by ReAct runtime
   surface: ReAct action/tool lane
   customized: no provider code; prompt and tool catalog influence output
   emits:
     action = call_tool
     tool_id = named_services.search_objects
     params.namespace = <configured namespace or provider-declared scoped namespace>

        |
        v

2. ReAct runtime and harness
   executor: ReAct v3 runtime in the consumer app process
   surface: decision/tool-execution phase
   customized: no namespace semantics
   work:
     validate the action packet
     apply multi-action strategy/harness rules
     choose ToolSubsystem for accepted tool calls

        |
        v

3. Consumer app tool config
   executor: agent_tool_config_from_bundle_props(...)
   surface: Consumer.config.surfaces.as_consumer.agents.<agent>.tools
   customized: yes, per consumer app and agent
   owns:
     namespace allow-list = <configured base namespace>
     visible operations = provider.about, object.search, object.host_file,
                          object.upsert, object.delete, ...
   maps provider operations to generic tool names:
     object.search    -> named_services.search_objects
     object.get       -> named_services.get_object
     object.host_file -> named_services.host_file
     object.upsert    -> named_services.upsert_object
     object.delete    -> named_services.delete_object

        |
        v

4. Generic named-service tool adapter
   executor: kdcube_ai_app...named_services_providers.tools
   surface: ToolSubsystem module call
   customized: configured namespace/provider endpoints, not provider object semantics
   builds:
     NamedServiceRequest.operation = object.search OR object.host_file OR ...
     NamedServiceRequest.namespace = original tool namespace argument
     NamedServiceRequest.context.base_namespace = configured base namespace
     NamedServiceRequest.object_ref = tool params object_ref, when present
     NamedServiceRequest.context.source = named_services.client_tool
     NamedServiceRequest.context.auth = current ReAct request/session

        |
        v

5. Generic provider discovery/transport
   executor: NamedServiceEndpoint / call_named_service_endpoint(...)
   surface: service discovery + bundle_registry/bundle_operation transport
   customized: provider endpoint config/discovery entry
   work:
     Discovery.resolve(operation, base_namespace, object_ref)
       -> Discovery.entry(provider_id=<provider id>, bundle_id=<provider app>)
     bundle_registry:
       call ProviderEntrypoint.named_services() in-process
     bundle_operation:
       call ProviderEntrypoint.@api(alias="named_service", route="operations")

        |
        v

6. Provider app backend
   executor: provider NamedServiceProvider.<operation>(ctx, request)
   surface: provider registry/API surface inside provider app
   customized: yes, provider owns namespace and object semantics
   examples:
     object.search:
       query provider storage/index and return bounded object descriptors
     object.host_file:
       read caller file descriptor under auth, store provider attachment,
       return Provider.object_ref for the new provider-owned file object
     object.upsert:
       validate provider schema and mutate the object
     object.get(response_mode=stream):
       stream provider-owned object bytes for react.pull

        |
        v

7. ReAct tool result
   executor: ToolSubsystem result handling in the consumer app
   surface: ReAct tool result artifact/timeline
   customized: no provider semantics
   result:
     success -> bounded NamedServiceResponse JSON or pull materialization rows
     failure -> provider error code/message preserved in the tool result
   side effect for object.search:
     emit subsystem artifact sub_type = named_service.search_results
     payload.items[] = generic context handles:
       ref/object_ref, label, summary, namespace, object_kind, mime

        |
        +--> Capable browser client
             executor: chat/scene/widget reducer and renderer
             surface: optional search-result UI
             customized: no namespace semantics
             work:
               render result rows as context objects
               click -> normal object.action resolver path
               drag  -> normal context attach/pin path

        |
        v

8. Next ReAct decision
   executor: model sees accepted tool result in the next round
   surface: ReAct timeline/context
   customized: model behavior only; provider result remains authoritative
```

Important ownership rules:

- The model sees generic tool names. The provider supplies schemas through
  `provider.about` and `object.schema`.
- The consumer app decides which named-service tools and namespaces the agent
  may call.
- The named-service tool adapter does not know how provider objects or scoped
  searches work. It only builds `NamedServiceRequest`.
- The provider app owns object parsing, permissions, search, mutation,
  hosting, streamed bytes, and any domain-specific error.
- Large bytes do not ride inside generic tool JSON. `react.pull` uses
  `object.get(response_mode=stream)` and writes provider bytes into ReAct
  `fi:` workspace files.

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
12. Implement `block.produce` when provider objects should become
    model-visible ReAct read blocks. Implement `block.render` when provider
    objects need custom prompt-render patches or explicit rendered
    representations.
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
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/agents/main.py
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
