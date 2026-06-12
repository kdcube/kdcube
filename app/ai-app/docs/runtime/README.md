---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
title: "Runtime Surfaces And Boundaries"
summary: "Index of KDCube runtime surfaces, execution boundaries, and the portable context guarantees used by bundles, tools, agents, and namespace services."
tags: ["runtime", "sdk", "bundles", "tools", "isolation", "context", "namespace-services"]
keywords:
  [
    "runtime surfaces",
    "runtime boundaries",
    "portable context",
    "comm_ctx",
    "bundle_call_context",
    "named service discovery",
    "subprocess tools",
    "iso runtime",
    "bundle runtime",
  ]
updated_at: 2026-06-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
---
# Runtime Surfaces And Boundaries

KDCube has several runtime surfaces. They share one platform vocabulary, but
they do not all share one Python process, one thread, or one set of live Python
objects. This directory documents what crosses those boundaries and which
surfaces must be reconstructed in the target runtime.

For the portable context contract, read
[Cross-Runtime Context](cross-runtime-context-README.md).

## Runtime Surface Map

| Surface | Where code runs | Main use | Context guarantee |
| --- | --- | --- | --- |
| Processor host runtime | chat processor process | chat turns, bundle entrypoints, local bundle operations | full request context, communicator, Redis/Postgres handles, bundle props/secrets APIs |
| REST/API bundle operation runtime | proc integration handler | `@api`, widget operations, public/internal bundle endpoints | request/session context is converted to `ExternalEventPayload` and bound around the method |
| MCP bundle runtime | proc integration handler or bundle-hosted MCP server | bundle MCP tools called by external agents | same request context contract as API when the call enters through KDCube ingress |
| Cron/job runtime | scheduler/worker process | `@cron`, `@on_job`, long-running platform work | headless or stored user context; no implicit browser session unless the job payload carries it |
| Data Bus handler runtime | Data Bus worker | durable bundle-scoped messages | `DataBusContext`, request-like auth context from message actor, current bundle id, optional comm replies |
| In-process tool runtime | same Python process as current turn | normal SDK tools and provider helpers | current `ContextVar` bindings are visible in the same async task context |
| Local subprocess tool runtime | child Python process on same host | crash containment for selected tools | portable spec plus selected contextvars are restored; live Python objects are rebuilt or absent |
| ISO Docker supervisor runtime | trusted supervisor process/container | tool brokering for isolated execution | descriptors, portable spec, communicator, settings/secrets, and tool subsystem are restored for supervisor |
| ISO Docker executor runtime | restricted generated-code process/container | untrusted generated code | minimal env, work/out surfaces, supervisor socket; no direct descriptors or platform secrets |
| Fargate exec supervisor/runtime | external ECS task | distributed isolated execution | same supervisor/executor contract as Docker, transported through the exec payload |
| Node/backend sidecar runtime | separate sidecar process | bundle-owned non-Python backend | context crosses only through the explicit sidecar bridge, not Python `ContextVar` snapshots |
| Browser/widget runtime | user browser | UI, SSE/Socket.IO/Data Bus clients | no server context; must use authenticated transport or platform-issued scoped tokens |

## Boundary Types

| Boundary | What crosses | What does not cross |
| --- | --- | --- |
| `asyncio` task / same process | task-local contextvars, current communicator, current request context | durable ownership; a later request needs a new binding |
| Worker thread | only context explicitly copied by the caller | implicit process globals that were not copied |
| Local subprocess | `PORTABLE_SPEC_JSON`, selected contextvars, runtime globals, output side files | live Redis/PG objects, live Python callbacks, nonserializable selectors |
| Docker/Fargate supervisor | descriptor payloads, portable spec, communicator spec, tool/module maps, storage config | host-only Python objects |
| Docker/Fargate executor | work/out paths, limited env, supervisor socket | descriptors, secrets, bundle storage roots, platform storage roots |
| Peer bundle local loop | request/session context, target bundle id, operation payload | browser cookies as raw replay; caller uses platform session/auth context |
| Redis Streams/Data Bus | durable JSON messages and handler results | conversation timeline unless an explicit bridge writes conversation events |
| SSE/Socket.IO relay | live envelopes to connected clients | durable truth; use storage/Data Bus/event lane for state |

## Portable Context Summary

The host does not serialize arbitrary Python objects into child runtimes. It
serializes a narrow runtime room:

```text
PORTABLE_SPEC_JSON
  model_config
  comm
  integrations
  accounting_storage
  contextvars
    run_ctx
    comm_ctx
      REQUEST_CONTEXT
      BUNDLE_ID
      BUNDLE_CALL_CONTEXT
      NAMED_SERVICE_DISCOVERY
    accounting
```

The most important rule is:

```text
portable context carries identity and descriptors;
runtime services are reconstructed in the target runtime.
```

Examples:

- `REQUEST_CONTEXT` restores tenant/project/user/routing metadata.
- `BUNDLE_CALL_CONTEXT` restores bundle-owned request-scoped metadata.
- `NAMED_SERVICE_DISCOVERY` restores the tenant/project Redis discovery scope
  for namespace-service provider lookup.
- if the explicit discovery descriptor is absent, restored `REQUEST_CONTEXT`
  tenant/project is enough to reconstruct the same discovery scope.
- Redis clients, communicators, model services, and storage helpers are
  reconstructed from runtime configuration; they are not serialized.

## Surface Families

| Surface family | Runtime crossing rule |
| --- | --- |
| Communicator | host exports a comm spec; child runtime rebuilds `ChatCommunicator`. Recording selectors cross only when JSON-portable. |
| Data Bus | messages are durable Redis Stream records. Handler lifecycle belongs to runtime workers. |
| Conversation event lane | conversation `external_events[]` are ordered and consumed by conversation/runtime orchestration, not by comm relay. |
| Namespace services | provider location is resolved from Named Service Discovery; calls use the best available runtime bridge while preserving auth context. |
| Bundle operations | same-KDCube calls should use local bundle operation/registry bridges when available; external HTTP is not required for local composition. |
| Accounting | accounting context is restored in child runtimes and writes through the configured accounting storage. |
| Artifacts | child runtimes write to runtime out/work surfaces; host merges expected side files and artifacts after execution. |

## Implementation Anchors

| Concern | Primary code |
| --- | --- |
| Portable spec build | `kdcube_ai_app/apps/chat/sdk/runtime/snapshot.py` |
| Child bootstrap restore | `kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py` |
| Request/context room | `kdcube_ai_app/apps/chat/sdk/runtime/comm_ctx.py` |
| Isolated execution | `kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py` |
| External Docker/Fargate runtime | `kdcube_ai_app/apps/chat/sdk/runtime/external/` |
| Bundle local bridges | `kdcube_ai_app/apps/chat/sdk/infra/bundle_operations.py` |
| Namespace services | `kdcube_ai_app/apps/chat/sdk/solutions/named_services_providers/` |
| Data Bus runtime | `kdcube_ai_app/apps/chat/sdk/runtime/data_bus/` |
