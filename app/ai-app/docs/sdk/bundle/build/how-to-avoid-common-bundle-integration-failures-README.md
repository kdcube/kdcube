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
updated_at: 2026-07-16
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/scene-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-understand-conversation-events-and-react-turns-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/client-transport-protocols-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-economics-integration-README.md
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
| Host product/client integration | iframe UI, direct browser client, host-server client, and backend-only app paths use different auth, origin, stream, and mutation contracts. |
| Widget visibility or config defaults | inherited widgets can exist but return "not visible to this user". |
| Live progress from a bundle operation | raw bundle WebSocket/SSE endpoints duplicate platform transport. |
| Browser-to-bundle durable mutations | Data Bus and conversation events have different ownership and ordering. |
| ReAct timeline/ANNOUNCE rendering | authored events and tool results need event-source policies, and conversation events cross lane/wake/processor/bundle-load/ReAct fences. |
| Canvas/task/memory/file refs | resolver ownership belongs to the namespace owner, not the composition bundle. |
| A third-party library, or adding `@venv` | a package that imports in your test `.venv` may not be in the proc base image; deciding membership by import ships undeclared or transitive deps. |
| Semantic search, background jobs, or task execution | economics must be wired at the operation boundary and visible through `[economics.enforcement]` traces. |

For the client-shape decision behind those failures, read
[How To Integrate With KDCube Apps](../../../how-to-integrate-with-kdcube-apps-README.md).

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
`apps`, `tools`, or `resources`. This includes bundle-local tool modules and
helpers loaded from configured tool refs.

The most seductive form of this mistake is a `try/except ImportError` fallback.
It looks defensive, but the `except` branch is a top-level bundle-local import,
so it is the same violation, just hidden:

```python
# WRONG — the except branch is a top-level bundle-local import
try:
    from .services.storage import IssueStore
except ImportError:  # comment usually claims "loaded as a top-level module"
    from services.storage import IssueStore
```

There is no flat-import case to support: **a bundle is always loaded as a Python
package**, so the package-relative form always resolves and the fallback is never
reached. Worse, if it ever were reached it could bind to another bundle's
already-loaded `services`/`tools` module. Delete the `try/except` and keep only
the package-relative import:

```python
from .services.storage import IssueStore
```

This is enforced: the shared bundle suite check
`test_bundle_python_uses_package_relative_bundle_local_imports` (run it alone with
`run_bundle_suite ... -k import_contract`) flags every top-level bundle-local
import, including one inside an `except` branch. If a package-relative import
raises `ImportError`, that is a real structure bug to fix — not something to paper
over with a fallback.

For configured tool refs:

| Tool source | `surfaces.as_consumer` shape |
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

## Recipe: Served-Widget Identity Is The Route

A served widget's own app identity is the ROUTE bundle id — the bundle URL it
is served from. The host CONFIG handshake's `defaultAppBundleId` is the HOST's
context. When a scene mounts your widget cross-bundle, letting the handshake
value overwrite the route value points every operation at the host's app and
the widget loads empty or errors.

Fix in one line: resolve the bundle id route-first; use `defaultAppBundleId`
only when the route yields none (standalone/main-view apps).

Read:

- [Scene recipe: Scene Components](../../../recipes/components/scene-README.md#scene-components)
- [Bundle Widget Integration](../bundle-widget-integration-README.md)

## Recipe: Widget UI — Settle The Design Before You Build

A recurring failure: an agent jumps straight into a monolithic `App.tsx` with
`useState` + raw `fetch` and ad-hoc CSS. The result looks off-brand next to the
rest of the product and is not modular, so it has to be rebuilt. Conclude the
design first, then implement.

Before writing widget UI, settle these four things:

1. **Use the kdcube design system — do not invent a look.** Reuse the shared
   tokens/feel from the chat widget (`…/sdk/solutions/chat/ui/widget/src/index.css`)
   or the `@kdcube/components-react` packages (`chat`, `canvas`). Inter font, the
   teal accent, the card/line/surface tokens, `.notice`/banner roles. Match the
   widgets already on the scene (memories, pinboard, chat); a new widget should
   look native beside them.
2. **Build modular React + Redux, not a monolith.** A store + feature slices
   (`createAsyncThunk`) + typed hooks (`useAppDispatch`/`useAppSelector`) + an
   `api/` layer (`settings.ts` host/scope, `client.ts` ops, `types.ts`) + split
   feature components + a shared `AppShell`. One giant `App.tsx` with inline
   `fetch` is the anti-pattern.
3. **Support every host the widget will run in, from the start.** A kdcube widget
   is an authenticated iframe (operations route; resolve baseUrl/tenant/project
   from the URL or the parent `CONFIG_REQUEST` bridge). If it is also a Telegram
   Mini App, the same widget must use the **public** route + the raw `initData`
   header, call `Telegram.WebApp.ready()/expand()`, and respect safe-area insets.
   Decide the host matrix before coding, not after.
4. **Copy an existing widget as the template.** Start from the SDK `memories`
   widget (`…/apps/chat/sdk/context/memory/ui/widget/memories`) — it already
   encodes the store/slice/api/component layout and the kdcube look.

Skipping this is what turns one UI task into "build it, then refactor it."

## Recipe: Shared Design-System Stylesheets Are Pure CSS

A stylesheet staged as a `shared_sources` entry is consumed by widget builds
that may have no CSS toolchain. A toolchain directive in the sheet — for
example `@import "tailwindcss"` — breaks every consuming bundle UI build that
lacks that toolchain.

Fix in one line: keep the shared sheet pure CSS; the consuming entry that has
the toolchain imports the directive in its own entry file (for example a local
`tailwind.css` imported before the shared sheet).

Read:

- [Widget Integration: shared sources](../../npm/widget-integration-README.md)

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

- [Bundle Client Communication: non-chat bundle events over the shared stream](../../../service/comm/client-transport-protocols-README.md#non-chat-app-events-over-the-shared-stream)
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

Before changing authored conversation events, followups, steers, snapshots, or
story-aware UI events, read
[Conversation Events And ReAct Turns](how-to-understand-conversation-events-and-react-turns-README.md).
Do not treat `external_events[]` as a direct bundle method call. The event is
accepted by ingress, ordered in the conversation lane, scheduled through a wake,
processed by `chat-proc`, then folded by the ReAct consumer if a turn owns the
lane.

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
`react.pull` can materialize it as a normal `conv:fi:` ref.

Publish authored events with transport kind `external_event` and the semantic
type nested in the payload. An event published with its semantic type as the
transport kind renders zero blocks and is invisible to the fold — for the full
lane-protocol facts see
[Conversation Events And ReAct Turns](how-to-understand-conversation-events-and-react-turns-README.md).

Read:

- [Conversation Events And ReAct Turns](how-to-understand-conversation-events-and-react-turns-README.md)
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
| `conv:fi:` | ReAct/chat artifact layer |
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

## Recipe: Keep Sync I/O Off The Serving Event Loop

Bundle code runs on the processor's serving event loop. Synchronous store
writes, sync Redis/HTTP clients, or `asyncio.run(...)` on a shared client
inside that loop stall every concurrent turn — the symptom is turn-wide
latency or a wedged turn, not an exception.

Fix in one line: use the async store/client variants where they exist;
offload genuinely sync work with `asyncio.to_thread(...)`; never call
`asyncio.run` on a client that belongs to the running loop.

Read:

- [Bundle Runtime](../bundle-runtime-README.md) — the async access contract

## Recipe: A `.venv` Import Is Not Base-Image Membership

The test `.venv` you prove locally is not the chat-processor container. A
successful `import <pkg>` there proves nothing about the base image — the
package may be a local or merely transitive install, which is exactly how an
undeclared dependency ships green in tests and then fails in proc. Deciding base
membership by import is the trap; the only authoritative test is a name grep of
the *declared* proc requirements.

Fix in one line: grep the package name in `requirements-chat-processor.txt`
(plus the shared `requirements-chat.txt`) — a hit on a top-level, pinned line is
declared and safe to import in-proc; no hit means own it via `@venv` +
`requirements.txt`, even if it imports in your `.venv`.

Read:

- [How To Assemble A Bundle With SDK Building Blocks](how-to-assemble-bundle-with-sdk-building-blocks-README.md) — the base-deps check before reaching for `@venv`
- [Bundle Venv](../bundle-venv-README.md)

## Recipe: The `os.environ` Rule — Never Use `os.environ`

KDCube apps define app/user properties and secrets in `bundles.yaml`,
`bundles.secrets.yaml`, and the platform user-scoped store. Runtime code uses
the helper contract: `bundle_prop`, `await get_secret("b:...")`,
`await get_secret("u:...")`, `await set_bundle_prop(...)`,
`await set_user_prop(...)`, `await set_user_secret(...)`, and `get_settings()`.
`os.environ` is not an app configuration channel. A knob exposed only as
`os.getenv(...)` is unreachable in a deployment: nothing declares it, no
operator surface can change it, and code silently keeps its hardcoded default.

Fix: declare the knob in the descriptor and read it through `bundle_prop(...)`
in the wrapper. If vendored standalone code legitimately reads env vars, keep
that only as its standalone fallback; the descriptor value wins when hosted.

One observed failure had an answer-model output budget only in an adapter
default. A payload-bearing tool call was cut mid-arguments at that ceiling on
every retry until the graph recursion limit, and no deployment surface could
raise the budget because it was not a property.

Read:

- [Bundle Properties And Secrets Lifecycle](../bundle-properties-and-secrets-lifecycle-README.md) — property read/write ownership
- [How To Write A Bundle](how-to-write-bundle-README.md) — the complete definition/read/set map per scope

## Recipe: Dynamic Context Crosses Fences Only Via The Portable Context

Execution in KDCube crosses async tasks, threads, subprocesses, and
Docker/Fargate runtimes. Process env and module-global state do not survive
those boundaries. A value stashed there can be present in proc and silently
absent inside an exec runtime, supervisor tool, or child fence.

Dynamic context, including identity, routing, accounting, communication, and
app-call context, crosses through the portable cross-runtime context. The host
snapshots it into `PORTABLE_SPEC_JSON`, the child bootstraps from it, and side
files reduce back to the host.

Fix: put per-request dynamic state in its carried context (`comm_ctx`, app-call
context, accounting context) or pass it explicitly in the child spec/arguments.
Never use env or module globals for it.

Read:

- [Cross-Runtime Context](../../../runtime/cross-runtime-context-README.md) — snapshot and restore anchors
- [Fenced Runtime Bootstrap And Reduce](../../../runtime/fenced-runtime-bootstrap-and-reduce-README.md) — the fence contract

## Triage Table

| Symptom | First Check |
| --- | --- |
| Widget route says not visible | widget visibility config, not only API visibility. |
| Static bundle loads HTML but assets 404 | built asset URLs are `/assets/...` instead of `./assets/...`. |
| Widget calls wrong host | API client used embedding page origin instead of runtime base URL. |
| Bundle import works in one process and fails in runtime | bundle-local imports are top-level instead of package-relative. |
| Third-party import works in tests but fails in proc runtime | the package is in your local `.venv`, not in the declared `requirements-chat-processor.txt` — own it via `@venv`, do not assume the base image. |
| Agent sees a JSON blob instead of useful context | missing event-source policy. |
| `react.pull` cannot resolve a compact ref | namespace rehoster or resolver was not registered in a loaded module. |
| UI mutation hangs or duplicates | Data Bus subject/object_ref/handler contract is incomplete. |
| Data Bus publish gets 429 or publish-limit rejection | `gateway.data_bus.ingress.publish_limits` is too low for the widget's package rate/size, or the widget is sending too many messages instead of batching. |
| Canvas pin cannot open/download | namespace owner resolver is not registered. |
| Cross-bundle-mounted widget loads empty or calls the wrong app | host handshake `defaultAppBundleId` overrode the widget's route bundle id. |
| Bundle UI build fails on a CSS import (postcss/tailwind not found) | a shared stylesheet carries a toolchain directive; move it to the consuming entry. |
| Every turn slow or wedged at once, no exception | sync I/O or `asyncio.run` on the serving event loop; use async variants or `to_thread`. |
| A runtime knob cannot be changed in a deployment | it is an env var or hardcoded default instead of a descriptor property read through `bundle_prop`. |
| A value or identity is present in proc but missing inside exec/subprocess/child runtime | it was stored in `os.environ` or a module global; dynamic state crosses fences only through portable context. |
| A tool-calling agent loops on retries until a recursion-limit error | the answer model's output budget may be too small for a complete payload-bearing tool call; expose and raise the descriptor property. |
| Authored event never appears in the timeline and the turn cannot complete | event published with a semantic transport kind instead of `external_event`. |
