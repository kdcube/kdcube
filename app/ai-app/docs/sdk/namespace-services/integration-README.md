---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
title: "Namespace Services: Integration Flow"
summary: "Visual host/client integration flow for namespace service providers, using task-tracker and versatile as the current reference path."
status: design
tags: ["sdk", "namespace-services", "integration", "task-tracker", "versatile", "scene", "canvas", "chat"]
updated_at: 2026-06-11
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

## System Picture

```text
Provider host bundle                         Client/composition bundle
task-tracker@1-0                             versatile@2026-03-31-13-36

issues/named_service.py                      bundle props:
  TaskIssueNamedServiceProvider                named_services.namespaces.task
    provider_id = task.issue                     clients.default_client.tools = ...
    namespace = task
    refs = task:issues/*

entrypoint.py                                 entrypoint.py
  named_services()                              _canvas_object_resolvers()
    registry.register(provider)                   register_configured_named_service_canvas_resolvers(...)
  on_bundle_load()
    Named Service Discovery register:
      namespace=task
      provider=task.issue
      bundle=task-tracker@1-0
      operations/object kinds/refs
                                                 _react_event_sources()
                                                   register_configured_named_service_artifact_rehosters(...)
                                                   register_configured_named_service_event_sources(...)

  @api(alias="named_service")                 tools_descriptor.py
    dispatch_named_service_api_request          tools_for_client(client_id, bundle_props)

        ^                                               |
        | Named Service Discovery resolves provider      |
        | request-bound bundle_registry calls owner      |
        +-----------------------------------------------+
```

## Object Action Flow

Opening a task card from canvas or chat:

```text
User clicks task:issues/issue_123
        |
        v
versatile canvas/chat widget calls canvas_object_action
        |
        v
versatile backend resolver registry sees namespace task
        |
        v
NamedServiceCanvasObjectResolver builds request:
  operation = object.action
  namespace = task
  object_ref = task:issues/issue_123
  action = open
        |
        v
Named Service Discovery selects a provider that supports the operation/ref:
  task-tracker@1-0 named_services() registry      # bundle_registry
  or task-tracker@1-0 / operations / named_service # bundle_operation
        |
        v
task-tracker provider returns:
  object_ref = task:issues/issue_123
  object = compact task issue descriptor
  ui_event.target_surface = task_tracker.issue_editor
        |
        v
versatile scene maps target_surface to task-tracker widget iframe
```

The task ref remains `task:issues/issue_123` the entire time. Canvas owns card
layout. Task-tracker owns task semantics.

## Provider Host Checklist

1. Define a provider class using `@named_service_provider(...)`.
2. Register that provider in a `NamedServiceRegistry`.
3. Expose `named_services()` so same-KDCube clients can call the registry
   directly.
4. Register the provider registry into Named Service Discovery during
   `on_bundle_load` after required local storage/indexes are ready.
5. Expose one bounded API operation, normally `@api(alias="named_service")`,
   when `bundle_operation` or external clients need an API facade.
6. Dispatch the operation with `dispatch_named_service_api_request(...)`.
7. Return canonical object descriptors under `ret.object`, list/search results
   under `ret.items`, common response metadata under `ret.attrs`, UI commands
   under `ret.ui_event`, and bounded provider-specific details under
   `ret.extra`.
8. Implement `object.schema` for each object kind that agents may mutate.
9. Implement `rehost` for attachment refs that should stream bytes into
   ReAct artifacts.
10. Implement `block.produce` / `block.render` when provider objects should
   become model-visible blocks.
11. Keep owner storage and mutation rules inside the provider bundle.

Current task-tracker reference points:

```text
applications/playground/bundles/task-tracker@1-0/issues/named_service.py
applications/playground/bundles/task-tracker@1-0/entrypoint.py
applications/playground/bundles/task-tracker@1-0/tests/test_named_service_provider.py
```

## Client Bundle Checklist

1. Configure `named_services.namespaces.<namespace>` and the client/tool policy
   that is allowed to use model-visible operations.
2. Register configured namespace resolvers into the canvas/chat resolver
   registry by enabling `clients.canvas.resolver.enabled`.
3. If model clients should call the provider, configure
   `named_services.namespaces.<namespace>.clients.<client_id>.tools`.
4. Extend tool specs with `extend_tool_specs_for_named_services(...)`.
5. Register artifact rehosters with
   `register_configured_named_service_artifact_rehosters(...)`.
6. Register namespace event sources with
   `register_configured_named_service_event_sources(...)` when lane events can
   carry `event_source_id: named_services.<namespace>`.
7. Route object open results through the scene surface registry.

Current versatile reference points:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools_descriptor.py
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
