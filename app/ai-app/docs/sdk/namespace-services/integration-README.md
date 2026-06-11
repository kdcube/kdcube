---
id: ks:docs/sdk/namespace-services/integration-README.md
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
  - ks:docs/sdk/namespace-services/README.md
  - ks:docs/sdk/namespace-services/providers-README.md
  - ks:docs/sdk/namespace-services/clients-README.md
  - ks:docs/sdk/solutions/scene/scene-composition-README.md
  - ks:docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
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
    provider_id = task.issue                     provider.bundle_id = task-tracker@1-0
    namespace = task                             provider.provider = task.issue
    refs = task:issues/*                         provider.operation = named_service
                                                  clients.default_client.tools = ...

entrypoint.py                                 entrypoint.py
  _named_services()                             _canvas_object_resolvers()
    registry.register(provider)                   register_configured_named_service_canvas_resolvers(...)

  @api(alias="named_service")                 tools_descriptor.py
    dispatch_named_service_api_request          tools_for_client(client_id, bundle_props)

        ^                                               |
        | request-bound local operation bridge           |
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
  provider = task.issue
  namespace = task
  object_ref = task:issues/issue_123
  action = open
        |
        v
request-bound local operation bridge calls:
  task-tracker@1-0 / operations / named_service
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
3. Expose one bounded API operation, normally `@api(alias="named_service")`.
4. Dispatch the operation with `dispatch_named_service_api_request(...)`.
5. Return canonical `object_ref`, compact object descriptors, `ui_event` for
   open/focus actions, and bounded data payloads.
6. Keep owner storage and mutation rules inside the provider bundle.

Current task-tracker reference points:

```text
applications/playground/bundles/task-tracker@1-0/issues/named_service.py
applications/playground/bundles/task-tracker@1-0/entrypoint.py
applications/playground/bundles/task-tracker@1-0/tests/test_named_service_provider.py
```

## Client Bundle Checklist

1. Configure `named_services.namespaces.<namespace>.provider`.
2. Register configured namespace resolvers into the canvas/chat resolver
   registry.
3. If model clients should call the provider, configure
   `named_services.namespaces.<namespace>.clients.<client_id>.tools`.
4. Extend tool specs with `extend_tool_specs_for_named_services(...)`.
5. Route object open results through the scene surface registry.

Current versatile reference points:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools_descriptor.py
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py
```

## Resolution Scope

The current resolution path is generic for configured namespaces:

```text
object_ref -> namespace -> configured provider endpoint -> provider operation
```

It is not yet a dynamic service-discovery system. A composition bundle must be
configured with the provider it is allowed to call. That is intentional for the
first implementation: it is predictable, auditable, and easy for bundle
builders to reproduce.
