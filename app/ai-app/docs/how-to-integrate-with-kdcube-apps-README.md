---
id: repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
title: "How To Integrate With KDCube Apps"
summary: "Canonical product-level integration guide for KDCube apps: iframe app UI, embedded KDCube control plane, direct host-browser clients, host-server clients, backend-only app surfaces, chat/event streams, Data Bus, named services, files, and auth/origin decisions."
tags: ["kdcube", "apps", "integration", "client", "iframe", "data-bus", "event-bus", "backend", "widget"]
keywords: ["integrate with kdcube apps", "host app kdcube integration", "direct browser client", "kdcube app backend only", "iframe kdcube app widget", "host server kdcube app api", "kdcube data bus client", "conversation event bus client", "external client surfaces"]
updated_at: 2026-06-14
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/what-you-can-do-with-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/embedding-kdcube-in-a-host-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/embedding-control-plane-frontend-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
---
# How To Integrate With KDCube Apps

Use this page first when an external website, product client, server, or
KDCube-hosted UI needs to integrate with a KDCube app.

A KDCube app is not only a widget. An app can expose backend surfaces without
shipping any UI, and a host product can either iframe KDCube-served UI or build
its own native UI that talks to KDCube app surfaces directly.

Implementation note: many lower-level SDK docs and runtime routes still use
the older term `bundle` for the deployable app package. This article uses
`app` for the product-facing concept and links to those lower-level contracts.

## Integration Modes

| Mode | UI owner | Backend owner | Use when |
| --- | --- | --- | --- |
| KDCube-served app UI in an iframe | KDCube app | KDCube app | The host wants to embed the app's widget or main view as a contained UI. |
| Embedded KDCube control plane | KDCube | KDCube + selected apps | The host wants the full KDCube chat/control-plane experience inside its product. |
| Direct host browser client | Host product | KDCube app surfaces | The host wants native UI while KDCube owns chat, agents, objects, files, or app state. |
| Host server/backend client | Host server | KDCube app surfaces | A server job, webhook handler, CRM pipeline, or product backend calls KDCube without a browser widget. |
| Backend-only app consumption | Host browser or server | KDCube app backend only | The app exposes operations, Data Bus handlers, named services, MCP, jobs, or agents, but no UI. |

These modes can be combined. A product can iframe one KDCube app UI, call
another app backend directly, and send conversation events to an agent in the
same deployment.

## Surface Map

| Surface | Client uses it for | Primary docs |
| --- | --- | --- |
| Static app UI routes | Load a KDCube-served widget or main view in an iframe or public launcher. | [Bundle Client UI](sdk/bundle/bundle-client-ui-README.md), [Bundle Widget Integration](sdk/bundle/bundle-widget-integration-README.md) |
| App operations/public APIs | Request/response calls to app backend code. | [Bundle Platform Integration](sdk/bundle/bundle-platform-integration-README.md), [Bundle Client Communication](sdk/bundle/bundle-client-communication-README.md) |
| Chat send path | Send user text, attachments, followups, steers, or authored external events into a conversation. | [Bundle Client Communication](sdk/bundle/bundle-client-communication-README.md), [Chat Stream Events](sdk/bundle/bundle-chat-stream-events-README.md) |
| Chat/event stream | Receive lifecycle, deltas, service events, compaction, completion, errors, and peer-targeted app events. | [Chat Stream Events](sdk/bundle/bundle-chat-stream-events-README.md) |
| Data Bus | Submit durable app-domain mutations such as board patches, issue edits, annotations, or app messages. | [Data Bus](service/comm/data-bus-README.md), [Conversation Event Bus And Data Bus](service/comm/conversation-event-bus-and-data-bus-README.md) |
| Named services | Expose or consume namespace-owned objects, refs, actions, relations, files, and provider contracts. | [Namespace Services](sdk/namespace-services/README.md) |
| Files/artifacts | Upload, host, download, cite, or materialize files through runtime or provider-owned refs. | [Bundle Runtime](sdk/bundle/bundle-runtime-README.md), [Namespace Services](sdk/namespace-services/README.md) |
| MCP | Tool/server integration for MCP-capable clients. | [Bundle Platform Integration](sdk/bundle/bundle-platform-integration-README.md), [MCP Tools](sdk/tools/mcp-README.md) |

## Mode 1: Iframe A KDCube App UI

The host page owns the outer page and decides where the iframe lives. KDCube
serves the app UI document and the app owns the UI source.

```text
Host.Client owns:
  Host.page
  Host.iframe_container
  optional Host.tokens

KDCube owns:
  KDCube.app_ui_url
  KDCube.frame_headers
  KDCube.runtime_config
  KDCube.session_stream

KDCube.App owns:
  App.UI.source
  App.Operation.aliases
  App.DataBus.handlers
  App.domain_state

Host.Client
  -> iframe src=KDCube.app_ui_url
  -> CONFIG_RESPONSE {Host.tokens?, tenant, project, app_id}

App.UI
  -> /api/integrations/.../operations/{alias}
  -> Socket.IO data_bus.publish or POST /sse/data_bus.publish
  -> /sse/stream or Socket.IO for service events
```

Use this shape when the host wants to reuse a KDCube app UI with minimal
host-side frontend code.

App UI responsibilities:

- declare the UI surface
- configure the matching source-folder build when the UI is a built app
- obtain runtime config from `GET /api/cp-frontend-config` or the parent
  `CONFIG_REQUEST` / `CONFIG_RESPONSE` handshake
- call KDCube through the UI frame/runtime origin, not the outer host page
  origin
- use app operations for direct commands
- use Data Bus for durable app-owned mutations
- include the stream/peer id when an operation response should target the same
  connected browser peer

Host responsibilities:

- choose same-origin, same-site subdomain, or cross-site embedding topology
- configure frame policy, CORS only when the host page itself fetches KDCube,
  and cookie/token handoff
- validate iframe `postMessage` origins
- render, resize, focus, or promote the iframe without assuming access to its
  cross-origin DOM

Read:

- [Embedding KDCube In A Host App](service/cicd/embedding-kdcube-in-a-host-app-README.md)
- [Embedding The Control Plane Frontend](service/cicd/embedding-control-plane-frontend-README.md)
- [Bundle Widget Integration](sdk/bundle/bundle-widget-integration-README.md)
- [UI Components Lifecycle](sdk/bundle/ui-components-lifecycle-README.md)

## Mode 2: Direct Host Browser Client

The host product owns the UI. It calls KDCube routes directly and renders the
results in native host components.

```text
Host.Client owns:
  Host.UI
  Host.auth_session or token handoff
  Host.stream_id
  Host.local_view_state

KDCube owns:
  KDCube.gateway_routes
  KDCube.chat_stream
  KDCube.conversation_event_bus
  KDCube.data_bus_ingress

KDCube.App owns:
  App.operations
  App.DataBus.handlers
  App.named_service_provider?
  App.domain_state

Host.Client
  -> GET /sse/stream or Socket.IO connect
  -> POST /sse/chat or Socket.IO chat_message
  -> POST /api/integrations/.../operations/{alias}
  -> data_bus.publish
  <- chat_* / chat_service / conv_status events
```

Use this shape for a custom website or application scene that should feel
native to the host product while KDCube provides chat, agents, streams, app
operations, Data Bus, or named-service object access.

Host-browser responsibilities:

- own the product UI and interaction model
- choose SSE or Socket.IO and preserve the stream/session identity
- send auth through headers, query params where required by SSE, or cookies
- treat chat send acknowledgements as admission, not final results
- subscribe to the shared chat stream event catalog
- use conversation `external_events[]` only when the user action should become
  conversation context for a turn
- use Data Bus when the user action mutates app-owned domain state
- call app operations for direct request/response backend commands
- respect tenant, project, app id, user, and auth context exactly as runtime
  config provides them

Read:

- [Bundle Client Communication](sdk/bundle/bundle-client-communication-README.md)
- [Bundle Chat Stream Events](sdk/bundle/bundle-chat-stream-events-README.md)
- [Conversation Event Bus And Data Bus](service/comm/conversation-event-bus-and-data-bus-README.md)
- [Data Bus](service/comm/data-bus-README.md)

## Mode 3: Host Server Or Backend Client

The host server calls KDCube as part of a backend workflow, webhook handler,
scheduled job, CRM/event pipeline, or product service. There may be no browser
client involved.

```text
Host.Server owns:
  Host.upstream_event
  Host.service_identity
  Host.idempotency_key
  Host.retry_policy

KDCube owns:
  KDCube.authz_context
  KDCube.app_invocation
  KDCube.data_bus_ingress
  KDCube.chat_enqueue

KDCube.App owns:
  App.backend_contract
  App.domain_state
  App.named_service_provider?

Host.Server
  -> App.Operation or public API
  -> Data Bus message {subject, object_ref, payload, idempotency_key}
  -> chat send path with external_events[] when an agent turn should see it
  <- operation response, stream/service event, or later domain state read
```

Use this shape when a server-side workflow integrates with a KDCube app backend,
not with a user-facing iframe.

Server responsibilities:

- carry the correct service/user authorization context
- keep idempotency keys for durable mutations
- distinguish admission responses from domain completion
- map upstream provider events into either Data Bus messages or conversation
  events based on ownership
- avoid bypassing app-owned domain storage by writing directly into storage
  internals
- expose only the host-side secrets needed by the host server; app secrets
  remain app-scoped

## Backend-Only App Integration

A KDCube app can be useful without a widget or main view.

Backend-only apps may expose:

- operations or public APIs
- Data Bus handlers
- named-service providers
- MCP endpoints
- scheduled jobs
- chat/on-message agents
- file or artifact hosting behavior

They do not need app UI configuration unless they ship a KDCube-served
frontend. A direct host browser client or host server can consume the backend
surfaces exactly the same way a KDCube-served UI would.

Backend-only authoring checklist:

- document the operation aliases, public APIs, Data Bus subjects,
  named-service namespaces, and auth model in the app interface
- configure gateway/Data Bus limits if browser or server clients publish
  durable messages
- decide whether user actions should enter the Conversation Event Bus, Data
  Bus, or a direct operation
- test discovery and invocation through the real runtime, not only local helper
  calls

## Choosing Event Bus, Data Bus, Or Operation

| User/client intent | Use | Reason |
| --- | --- | --- |
| "Tell the agent about this and let it react." | Conversation Event Bus via `/sse/chat` or Socket.IO `chat_message` with `external_events[]` | The event becomes ordered conversation context. |
| "Mutate durable app-owned state." | Data Bus | App handler owns ordering, idempotency, retries, and state mutation. |
| "Run a direct command and return a response now." | App operation/public API | Request/response command, usually from app UI or host UI. |
| "Read or act on namespace-owned objects." | Named service provider/client contract | Namespace owner defines refs, schemas, actions, files, and resolver behavior. |
| "Show an existing KDCube UI." | Iframe app UI/control plane | Reuse KDCube-served frontend. |

## Auth And Origin Decision

Browser integrations must decide two separate things:

1. Can the host page frame KDCube?
2. Can the browser or iframe carry auth to KDCube?

Server integrations instead decide which service/user auth context the server
is allowed to carry.

```text
Browser iframe:
  Host.Client.frame_policy -> proxy.frame_embedding
  Host.Client.auth_transport -> cookie or CONFIG_RESPONSE token handoff

Direct browser fetch:
  Host.Client.fetch_origin -> cors.allow_origins
  Host.Client.auth_transport -> headers, SSE query params, or cookies

Server call:
  Host.Server.identity -> service/user authorization context
  Host.Server.network -> backend route/auth policy
```

Embedding and direct browser fetch are related but different. A CORS allowlist
does not allow iframe embedding, and frame policy does not allow the outer host
page to fetch KDCube.

## Minimum Client Checklists

### Iframe App UI

- route loads from KDCube app UI URL
- frame headers allow the host page
- auth works after iframe reload
- runtime config contains base URL, tenant, project, app id, and auth
- operations use KDCube origin
- durable mutations use Data Bus where appropriate
- stream events render admission, progress, completion, and errors

### Direct Host Browser Client

- opens `/sse/stream` or Socket.IO with stable stream/session identity
- sends `/sse/chat` or `chat_message` with the correct tenant/project/app
  context
- handles `followup_accepted` and `steer_accepted` as admission only
- handles all known chat stream events and treats unknown semantic event types
  as generic visible service/progress events
- uses Data Bus for domain mutations
- uses operations for request/response backend commands
- does not assume widget-only config handshakes exist

### Host Server Client

- carries authorized service/user context
- uses idempotency keys for durable messages
- distinguishes operation completion from Data Bus handler completion
- records enough upstream ids to replay safely
- never writes app-owned domain state behind the app provider/handler

## Read Next

- If you are embedding an iframe, start with
  [Embedding KDCube In A Host App](service/cicd/embedding-kdcube-in-a-host-app-README.md).
- If you are building a KDCube-served app UI, start with
  [Bundle Widget Integration](sdk/bundle/bundle-widget-integration-README.md).
- If you are writing a direct browser client, start with
  [Bundle Client Communication](sdk/bundle/bundle-client-communication-README.md) and
  [Bundle Chat Stream Events](sdk/bundle/bundle-chat-stream-events-README.md).
- If you are sending durable domain messages, start with
  [Conversation Event Bus And Data Bus](service/comm/conversation-event-bus-and-data-bus-README.md).
- If you are exposing or consuming namespace-owned objects, start with
  [Namespace Services](sdk/namespace-services/README.md).
- If you are writing or wrapping a KDCube app backend, start with
  [How To Write A Bundle](sdk/bundle/build/how-to-write-bundle-README.md) and
  [Bundle Platform Integration](sdk/bundle/bundle-platform-integration-README.md).
