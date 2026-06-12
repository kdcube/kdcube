---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
title: "How To Avoid Common Bundle Integration Failures"
summary: "Short bundle implementation recipes for recurring integration mistakes: bundle-local imports, widget origins and assets, visibility gates, live events, Data Bus boundaries, authored events, subsystem mounting, and resolver ownership."
tags: ["sdk", "bundle", "recipes", "integration", "widgets", "events", "data-bus", "imports", "resolvers"]
keywords:
  [
    "bundle integration failures",
    "bundle gotchas",
    "bundle local imports",
    "widget origin",
    "relative vite assets",
    "not visible to this user",
    "bundle live events",
    "data bus boundary",
    "authored external events",
    "resolver ownership",
  ]
updated_at: 2026-06-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
---
# How To Avoid Common Bundle Integration Failures

Use this page after the navigation and assembly docs have routed you to the
right implementation area. It collects recurring bundle failure modes that are
small enough to miss and large enough to break runtime behavior.

This page is not a complete subsystem checklist. If you are mounting memory,
canvas, tasks, Telegram, delivery, or another reusable SDK subsystem, also read
[Bundle Subsystem Integration](../bundle-subsystem-integration-README.md).

## When To Open This Page

| You are changing | Keep this page open because |
| --- | --- |
| Bundle-local Python modules or tool descriptors | top-level imports can pass locally and fail under bundle import isolation. |
| Widget/static UI code | wrong origins and absolute assets break embedded bundle UIs. |
| Widget visibility or config defaults | inherited widgets can exist but return "not visible to this user". |
| Live progress from a bundle operation | raw bundle WebSocket/SSE endpoints duplicate platform transport. |
| Browser-to-bundle durable mutations | Data Bus and conversation events have different ownership and ordering. |
| ReAct timeline/ANNOUNCE rendering | authored events and tool results need event-source policies. |
| Canvas/task/memory/file refs | resolver ownership belongs to the namespace owner, not the composition bundle. |

## Recipe: Bundle-Local Python Imports

Bundle-local code must use package-relative imports.

Good:

```python
from .services.storage import IssueStore
from .issues.tools import TaskToolsPlugin
```

Bad:

```python
from services.storage import IssueStore
from issues.tools import TaskToolsPlugin
```

Do not import bundle-local folders as top-level packages such as `services`,
`apps`, `tools`, or `resources`. This includes `tools_descriptor.py` and
bundle-local tool modules.

For tool descriptors:

| Tool source | Descriptor shape |
| --- | --- |
| bundle-local file | `ref: "tools/name.py"` |
| installed SDK or external module | `module: "kdcube_ai_app...."` |

Read:

- [Bundle Runtime: critical bundle-local import rule](../bundle-runtime-README.md#critical-bundle-local-import-rule)
- [Custom Tools: bundle-local imports from ref tools](../../tools/custom-tools-README.md#bundle-local-imports-from-ref-tools)

## Recipe: Widget Origin And Static Assets

Browser-facing bundle code must call KDCube through the KDCube frame/runtime
origin, not the embedding site origin.

Use this order for API base URL:

```text
CONFIG_REQUEST / CONN_RESPONSE baseUrl
  -> widget frame window.location.origin
```

Do not use:

```text
window.top.location
document.referrer
embedding host page URL
```

Buildable `ui/main` apps and source-folder widgets must emit relative asset
URLs. For Vite:

```ts
export default defineConfig({
  base: "./",
})
```

Verify built `index.html` contains:

```html
<script type="module" crossorigin src="./assets/index-....js"></script>
<link rel="stylesheet" crossorigin href="./assets/index-....css">
```

It must not contain `/assets/...`.

Read:

- [Bundle Widget Integration: source-folder widget apps](../bundle-widget-integration-README.md#source-folder-widget-apps)
- [Bundle Widget Integration: frame origin and API base URL](../bundle-widget-integration-README.md#frame-origin-and-api-base-url)

## Recipe: Widget Visibility Gates

If a widget route returns `"Bundle widget <alias> is not visible to this user"`,
check the gates separately.

| Gate | Config path | Meaning |
| --- | --- | --- |
| widget surface | `enabled.widget.<alias>` | exposes or hides the decorated widget route. |
| static widget app | `ui.widgets.<alias>.enabled` | enables the built source-folder widget app. |
| route visibility | `visibility.widget.<alias>.user_types` and `.roles` | restricts who can fetch the widget route. |

`user_types: []` means no user-type restriction. A non-empty list restricts the
route to those exact SDK user types. Do not copy API visibility into widget
visibility unless that is the explicit product policy.

For inherited SDK widgets, the alias must match the inherited decorator alias.
Config under `ui.widgets.memories` configures the existing `memories` widget; it
does not create a new platform widget surface by itself.

Read:

- [Bundle Subsystem Integration: visibility and enablement](../bundle-subsystem-integration-README.md#3-visibility-and-enablement)
- [Bundle Widget Integration](../bundle-widget-integration-README.md)

## Recipe: Live Events From Bundle Operations

Do not create bundle-owned raw WebSocket or raw SSE endpoints just to stream
progress from a bundle operation.

Use the platform session stream:

```text
browser opens /sse/stream or Socket.IO
browser calls /api/integrations/.../operations/...
browser passes KDC-Stream-ID
bundle emits comm.service_event(...) through request-bound comm
platform routes the event to the connected client
```

Bundle code should use the request-bound communicator:

```python
comm = get_current_comm()
if comm:
    await comm.service_event({"type": "task_tracker.progress", "message": "..."})
```

Use Data Bus for durable bundle-scoped inbound mutations. Use the shared
session stream for operation progress and UI feedback.

Read:

- [Bundle Client Communication: non-chat bundle events over the shared stream](../bundle-client-communication-README.md#non-chat-bundle-events-over-the-shared-stream)
- [Bundle Transports](../bundle-transports-README.md)

## Recipe: Data Bus Is Not The Conversation Bus

The conversation bus is for conversation turns, external conversation events,
chat stream events, and ReAct timeline behavior.

The Data Bus is for durable, bundle-scoped inbound messages such as:

```json
{
  "subject": "task_tracker.canvas.patch",
  "object_ref": "canvas:user:main",
  "payload": {"operations": []}
}
```

Keep these boundaries:

| Need | Use |
| --- | --- |
| user chat prompt or follow-up | conversation ingress |
| authored event that should be part of ReAct timeline | conversation `external_events[]` |
| browser mutation for bundle-owned object | Data Bus |
| operation progress back to current browser | shared session stream |
| project-wide compact UI refresh | project event broadcast |

Operational rules:

- `data_bus.publish` is admitted before durable stream write by the gateway
  Data Bus publish limiter.
- Tune package/message/byte limits in `gateway.yaml` under
  `gateway.data_bus.ingress.publish_limits`; do not put these platform limits
  in bundle props.
- Treat publish-limit rejection as "not accepted". The client should surface
  the rejection and retry later or batch less aggressively; the bundle handler
  will not see rejected messages.

Read:

- [Conversation Event Bus And Data Bus](../../../service/comm/conversation-event-bus-and-data-bus-README.md)
- [Data Bus](../../../service/comm/data-bus-README.md)
- [Bus Routing And Partitioning](../../../service/comm/bus-routing-and-partitioning-README.md)
- [Gateway Descriptor: Data Bus publish limits](../../../configuration/gateway-descriptor-README.md#data_buspublish_limits)

## Recipe: Authored Events And Tool Result Rendering

Use authored external events for story-aware UI moments:

- wizard assistance
- canvas review
- saved snapshot
- uploaded evidence
- task/story context attachment

Tools are event sources too:

```text
tool_id        -> event_source_id
tool_call_id   -> event_id
```

Bind event-source policies by `react_phase`. `block_production` owns how a tool
result or authored event becomes timeline blocks and artifact rows. ANNOUNCE
policies should preserve current context facts that the agent needs for the
turn.

If event data carries a compact owner-domain artifact URI such as `nmsp:...`,
`mem:...`, or `cnv:...`,
register an artifact namespace rehoster in a loaded tool or event module so
`react.pull` can materialize it as a normal `fi:` ref.

Read:

- [Bundle Events](../bundle-events-README.md)
- [React Event Sources](../../agents/react/event-source/event-source-README.md)

## Recipe: Mount The Whole Subsystem

An SDK subsystem is a vertical slice:

```text
entrypoint mixin/decorators
  -> config defaults
  -> visibility
  -> UI source
  -> APIs
  -> tools and skills
  -> event policies and resolvers
  -> storage/schema/user-scope hooks
  -> tests
```

Do not copy one widget or one helper from a subsystem and call it integrated.
That creates the recurring half-mounted state: icon visible, route hidden;
widget loaded, APIs missing; tools exposed, instructions absent; pins visible,
resolver absent.

Read:

- [Bundle Subsystem Integration](../bundle-subsystem-integration-README.md)

## Recipe: Resolver Ownership

Object resolver behavior belongs to the namespace owner.

| Namespace | Owner |
| --- | --- |
| `fi:` | ReAct/chat artifact layer |
| `mem:` | memory subsystem |
| `task:` | task or issue subsystem |
| `cnv:` | canvas subsystem |
| provider-defined refs | named-service providers, MCP/search surfaces, or explicit rehosters |

A composition bundle registers the resolvers it mounts. It should not duplicate
the resolver behavior in unrelated files. For example, canvas can ask
`resolver("task").open("task:issues/...")`, but task owns what "open issue"
means.

For a namespace **another bundle** owns (`task:`), do not write a resolver —
configure `named_services.namespaces.<ns>.provider` and let the generic
namespace-service resolver call the owner over the runtime bridge. A bundle only
writes a concrete resolver for namespaces it owns itself.

Read:

- [Namespace Services](../../namespace-services/README.md) — calling another bundle's namespace
- [Event Hub Resolver And Policy Registration](../../solutions/event-hub/resolver-and-policy-registration-README.md)
- [Canvas Pin Integration](../../solutions/canvas/pin-integration-README.md)

## Triage Table

| Symptom | First Check |
| --- | --- |
| Widget route says not visible | widget visibility config, not only API visibility. |
| Static bundle loads HTML but assets 404 | built asset URLs are `/assets/...` instead of `./assets/...`. |
| Widget calls wrong host | API client used embedding page origin instead of runtime base URL. |
| Bundle import works in one process and fails in runtime | bundle-local imports are top-level instead of package-relative. |
| Agent sees a JSON blob instead of useful context | missing event-source policy. |
| `react.pull` cannot resolve a compact ref | namespace rehoster or resolver was not registered in a loaded module. |
| UI mutation hangs or duplicates | Data Bus subject/object_ref/handler contract is incomplete. |
| Data Bus publish gets 429 or publish-limit rejection | `gateway.data_bus.ingress.publish_limits` is too low for the widget's package rate/size, or the widget is sending too many messages instead of batching. |
| Canvas pin cannot open/download | namespace owner resolver is not registered. |
