---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
title: "How To Assemble A Bundle With SDK Building Blocks"
summary: "Tier 1 bundle-builder map for choosing reusable KDCube SDK and platform blocks before writing custom bundle services: tools, event sources, agents, storage, widgets, jobs, integrations, and solutions."
tags: ["sdk", "bundle", "tier-1", "building-blocks", "integrations", "solutions", "tools"]
keywords: ["bundle building blocks", "sdk integrations", "sdk solutions", "bundle assembly map", "reuse sdk components", "telegram integration", "email integration", "tasks solution", "delivery integration", "shared sdk widget components", "built in tools", "react tools", "bundle events", "event sources", "artifact rehosters"]
updated_at: 2026-06-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/tasks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/browser/browser-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/sdk-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/design/bundle-loader-import-isolation-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/memory-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# How To Assemble A Bundle With SDK Building Blocks

Use this page before implementing a new subsystem in a bundle.

The goal is to assemble product behavior from reusable KDCube blocks where the
platform already owns the mechanics, and keep bundle code focused on product
policy, route aliases, prompts, UI composition, and user-scope decisions.

If you landed here directly, first read
[how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md).
This page is the SDK/platform block map, not the whole bundle-building route.
Doc front-matter IDs are platform doc IDs. In a local checkout, read the files
directly under `repo:kdcube-ai-app/app/ai-app/docs/...`.

For the local runtime command flow, use the canonical schemas in
[how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas](how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas):
`init` once, `refresh` for platform source/image changes, and
`bundle config apply` / `bundle reload` for bundle descriptor and source
changes.

## Assembly Rule

For each feature, choose the closest existing block first:

```text
product need
  -> SDK/platform block
  -> bundle binding hooks
  -> product policy and UI
```

Write custom code when the product policy is new, the provider is not covered,
or the bundle needs domain-specific storage and prompts.

When a feature becomes reusable across bundles, move it into an SDK integration
or solution package and update this page.

If the selected block is an existing SDK subsystem, integration means mounting
the whole subsystem contract, not only importing its widget or one helper. Read
[Bundle Subsystem Integration](../bundle-subsystem-integration-README.md) and
wire entrypoint mixins/decorators, config, visibility, UI source, APIs, tools,
skills, event policies, resolvers, storage/schema hooks, transport, and tests
as one unit.

For chat-owning bundles that mount an agent-facing SDK subsystem, wire the
agent surface explicitly:

1. Import the subsystem's stable instruction constant, for example
   `CANVAS_REACT_ADDITIONAL_INSTRUCTIONS` from
   `kdcube_ai_app.apps.chat.sdk.solutions.canvas.instructions`.
2. Compose it with the bundle/domain instruction in bundle code. Keep this
   stable; do not put per-turn state or selected objects into cached
   instructions.
3. Pass the composed text as `additional_instructions` when constructing ReAct.
4. Register the subsystem tools and event visibility separately:
   `surfaces.as_consumer` exposes model-callable tools, while
   `event_source_specs` add event sources, policies, event-source readers, and
   namespace rehosters. Instructions alone do not expose tools, render
   timeline/ANNOUNCE blocks, or make refs resolvable.

After selecting the block, keep
[How To Avoid Common Bundle Integration Failures](how-to-avoid-common-bundle-integration-failures-README.md)
open while implementing. That page owns the recurring sharp rules for
bundle-local imports, widget origins/assets, visibility gates, live events,
Data Bus boundaries, authored events, subsystem mounting, and resolver
ownership.

## Current Reusable Blocks

| Need | Use | Primary docs |
| --- | --- | --- |
| Durable user memories, memory widget, memory tools, reconciliation, and `mem:` refs | `MemoryEntrypointMixin` / `BaseEntrypointWithMemory` plus `kdcube_ai_app.apps.chat.sdk.context.memory` | [Bundle Subsystem Integration](../bundle-subsystem-integration-README.md), [User Memories Overview](../../memory/user-memories-overview-README.md) |
| Versioned collaborative board, pins, canvas tools, canvas ANNOUNCE/timeline policies, and object resolver registry | `kdcube_ai_app.apps.chat.sdk.solutions.canvas` | [Canvas SDK Solution](../../solutions/canvas/canvas-sdk-solution-README.md), [Bundle Subsystem Integration](../bundle-subsystem-integration-README.md) |
| Saved tasks, schedules, fresh executions, execution journals, output recovery | `kdcube_ai_app.apps.chat.sdk.solutions.tasks` | [Tasks SDK Solution](../../solutions/tasks-README.md) |
| Gmail/iCloud accounts, OAuth/settings, email attachment materialization, Email MCP, Claude Code email processing | `kdcube_ai_app.apps.chat.sdk.integrations.email` | [Email Integration](../../integrations/email/README.md) |
| Telegram webhook, Bot API rendering, progress streaming, Mini App auth, widget operations, user registry, signed downloads | `kdcube_ai_app.apps.chat.sdk.integrations.telegram` | [Telegram Integration](../../integrations/telegram/README.md) |
| Local public HTTPS origin for provider callbacks, Telegram webhooks, OAuth callbacks, and remote-control style integrations while KDCube runs on localhost | one ngrok HTTPS URL through a local reverse proxy into frontend, ingress, and proc | [Serving Local KDCube With Ngrok](../../../service/cicd/ngrok-README.md) |
| Explicit report delivery to email/Telegram with delivered-file metadata | `kdcube_ai_app.apps.chat.sdk.integrations.delivery` | [Email Integration](../../integrations/email/email-README.md), [Telegram Integration](../../integrations/telegram/telegram-README.md) |
| Web search and web fetch with source-pool provenance | `web_tools` | [SDK Tools](../../tools/sdk-tools-README.md) |
| Policy-driven tool result rendering | tool `@event_source(...)` declarations plus `react_phase=block_production` policies | [Bundle Events](../bundle-events-README.md), [React Event Sources](../../agents/react/event-source/event-source-README.md) |
| Story-aware wizard/canvas/chat events and snapshots | authored external events with `payload.target.agent_id`, `story_kind`, `story_id`, and `external_events[].event_source_id` | [Bundle Events](../bundle-events-README.md), [External Events](../../events/external-events-README.md) |
| Bundle/domain artifact refs visible to ReAct | `@artifact_namespace_rehoster(...)` for compact owner namespaces such as `nmsp:...`, `mem:...`, or `cnv:...`; `react.pull` returns the materialized `fi:` path | [Bundle Events](../bundle-events-README.md), [React Event Source](../../agents/react/event-source/event-source-README.md), [React Turn Workspace](../../agents/react/react-turn-workspace-README.md) |
| Real browser verification for generated HTML, widgets, and local browser flows | `browser_tools`, shared Playwright backend, per-turn BrowserContext | [Browser Tools](../../integrations/browser/browser-tools-README.md), [Playwright Backend](../../integrations/browser/playwright-README.md) |
| ReAct-side artifact recovery, search, and precise text editing | `react.pull`, `react.checkout`, `react.rg`, `react.read`, `react.patch` | [React Turn Workspace](../../agents/react/react-turn-workspace-README.md), [React Runtime Configuration](../../agents/react/runtime-configuration-README.md) |
| PDF, DOCX, PPTX, PNG, HTML generation | `rendering_tools` plus public rendering skills | [SDK Tools](../../tools/sdk-tools-README.md) |
| Isolated code execution and generated-code work | `exec_tools`, isolated runtime, tool bridge | [Bundle Agent Integration](../bundle-agent-integration-README.md), [SDK Tools](../../tools/sdk-tools-README.md) |
| Context, attachments, hosted files, and conversation-scoped reads | `ctx_tools`, `io_tools`, hosting/runtime APIs | [Bundle Runtime](../bundle-runtime-README.md), [SDK Tools](../../tools/sdk-tools-README.md) |
| ReAct agent with bundle tools and skills | `BaseWorkflow.build_react(...)`, `surfaces.as_consumer`, `skills_descriptor.py`; skills are discovered from core SDK, SDK solution roots, and bundle `CUSTOM_SKILLS_ROOT`, then filtered by exact consumer id | [Bundle Agent Integration](../bundle-agent-integration-README.md) |
| Per-role model routing and temporary model strength selection | `config.role_models` for defaults/descriptor overrides; `bundle_call_context.role_models` for one API/MCP/cron/chat/job call | [Bundle Agent Integration](../bundle-agent-integration-README.md#model-selection-for-agent-roles), [Bundle Runtime](../bundle-runtime-README.md#request-scoped-role-model-override) |
| Bundle-served MCP endpoint | `@mcp(...)` | [Bundle Platform Integration](../bundle-platform-integration-README.md), [MCP Tools](../../tools/mcp-README.md) |
| Claude Code subagent with scoped MCP/tools | `ClaudeCodeAgent`, `ClaudeCodeWorkspaceConfig` | [Bundle Agent Integration](../bundle-agent-integration-README.md) |
| Browser widget or Mini App | `@ui_widget(...)`, source-folder widget build, operations/public APIs | [Bundle Widget Integration](../bundle-widget-integration-README.md) |
| Expose a namespace (`task:`, …) of objects/actions for other bundles to call, **or** consume another bundle's namespace as canvas pins / chat chips / agent tools | Owner side: `named_service` API operation + `NamedServiceRegistry`. Consumer side: `named_services.namespaces.<ns>.provider` config + generic resolver — no shared code, configured discovery | [Namespace Services](../../namespace-services/README.md) (index → Providers / Clients / Integration), [Bundle Subsystem Integration](../bundle-subsystem-integration-README.md) |
| Widget expand to fullscreen/overlay when embedded as an iframe | host-driven `kdcube-widget-view` / `kdcube-set-view` postMessage; host promotes the same iframe (no reload) | [Frame View Contract](../bundle-widget-integration-README.md#frame-view-contract-host-driven-expand) |
| Live events from a non-chat widget/API operation to the browser | `/sse/stream` or Socket.IO plus `KDC-Stream-ID`; bundle emits `comm.service_event(...)` from request-bound context | [Bundle Client Communication](../bundle-client-communication-README.md#non-chat-bundle-events-over-the-shared-stream), [Bundle Transports](../bundle-transports-README.md#71-communicator-output) |
| Tenant/project widget refresh events | SSE `/sse/stream?project_events=true`; bundle emits compact `comm.project_event(...)` snapshots | [Bundle Client Communication](../bundle-client-communication-README.md#tenantproject-sse-broadcast), [Bundle Platform Integration](../bundle-platform-integration-README.md#bundle-to-client-event-scopes) |
| Durable browser/widget mutations for bundle-owned objects | Socket.IO `data_bus.publish` or `POST /sse/data_bus.publish` with `messages[]`; bundle handles via `@data_bus_handler(...)` | [Conversation Event Bus And Data Bus](../../../service/comm/conversation-event-bus-and-data-bus-README.md), [Data Bus](../../../service/comm/data-bus-README.md), [How To Write A Bundle](how-to-write-bundle-README.md#data-bus-handler-path) |
| Shared widget UI pieces such as User Memory and Telegram admin/channels panels | `ui.widgets.<alias>.shared_sources`; SDK source is materialized into the consuming bundle's widget build and served from that bundle's storage root | [Shared UI Source Materialization](../bundle-widget-integration-README.md#shared-ui-source-materialization) |
| Scheduled scan and background execution | `@cron(...)`, `@on_job`, jobs stream; use Tasks Solution for saved task execution | [Scheduled Jobs](../bundle-scheduled-jobs-README.md), [Tasks SDK Solution](../../solutions/tasks-README.md) |
| Local mutable files, generated indexes, git working copies, runtime caches | bundle storage helpers, `BundleArtifactStorage`, KV cache, git helpers | [Bundle Storage And Cache](../bundle-storage-and-cache-README.md) |
| Node/TypeScript backend inside a bundle | Python bundle shell + Node sidecar bridge | [Bundle Node Backend Bridge](../bundle-node-backend-bridge-README.md) |
| Bundle-specific Python dependencies | `@venv(...)` | [Bundle Venv](../bundle-venv-README.md) |

## Where Blocks Are Wired

| File or layer | What belongs there |
| --- | --- |
| `entrypoint.py` | Decorators, route aliases, SDK module configuration, storage-root and user-scope hooks, product role policy. |
| `surfaces.as_consumer` | Tool aliases for SDK tool modules and bundle-local tool modules used by the agent. Bundle-local tools should use `ref: "tools/name.py"` and package-relative imports; `module` is for installed modules. |
| `event_source_specs` | Event-source modules loaded into ReAct, including authored UI event declarations, policies, event-source readers, and custom artifact namespace rehosters. These are runtime/event surfaces, not callable tools. |
| `events/*.py` | Bundle-owned event-source declarations, phase policy bindings, and rehosters for domain artifact namespaces. |
| `skills_descriptor.py` | Bundle-local skill root plus `AGENTS_CONFIG` filters for core SDK skills, SDK solution skills such as `task.*`, and bundle-local product skills. |
| skill `tools.yaml` | Tool metadata for a skill; add `required: true` for tool ids that must exist before that skill is shown or loaded. |
| `config/bundles.template.yaml` | Deployment-scoped non-secret props that enable/configure the block. |
| `config/bundles.secrets.template.yaml` | Deployment-scoped secrets such as bot tokens, OAuth client secrets, signing keys. |
| `interface/README.md` | Bundle-facing contract for widget aliases, API/MCP/cron/job routes, public-auth rules, payload shapes, and controlling config keys. |
| user settings UI | User-owned credentials and choices, such as personal email accounts. |
| `docs/integrations/*` in a bundle | Operator homework outside KDCube, such as BotFather or Google Cloud setup. |
| `docs/design/*` in a bundle | Current product boundary: which SDK blocks are used and what policy remains in the bundle. |

For bundles with buildable main UI or source-folder widgets, the entrypoint
should inherit a concrete `BaseEntrypoint` family class unless it deliberately
implements the same UI build contract. The `@ui_widget(...)` decorator declares
the surface, but the `BaseEntrypoint` family provides the default static
UI/widget build and refresh path. See
[Bundle Entrypoint Classes](../bundle-entrypoint-classes-README.md).
If that entrypoint overrides `on_bundle_load(...)`, call
`await super().on_bundle_load(**kwargs)` after applying needed runtime handles
from `kwargs`; otherwise startup preload can import the bundle without building
its configured widget assets.

Keep the decorated bundle entrypoint and the per-message orchestrator separate:
decorate the `BaseEntrypoint`-family class, and create `BaseWorkflow`
subclasses inside the turn execution. Do not use a `BaseWorkflow` subclass as a
singleton bundle entrypoint.

## Common Product Recipes

### Chat Agent With Files, Search, And Reports

```text
React workflow
  -> web_tools for research
  -> browser_tools for generated HTML/widget verification when needed
  -> react.rg/read/patch for precise artifact editing
  -> rendering_tools for PDF/DOCX/PPTX/PNG/HTML
  -> delivery integration for email/Telegram delivery
  -> hosted file metadata in turn timeline
```

Bundle code owns the prompt, allowed tool aliases, delivery target policy, and
UI route aliases.

### Task Automation App

```text
Tasks SDK Solution
  -> task storage and search
  -> execution journals and artifacts
  -> due scan + job handler
  -> task/job skills and tools
  -> widget operation helpers
```

Bundle code owns product-specific task wording, user identity resolution,
widget composition, and route exposure.

Tasks skills are discovered from the SDK solution root even without a
bundle-local `skills/` folder. They declare required task tools, so they are
normally omitted automatically when those tools are not in the active React tool
catalog. Use `AGENTS_CONFIG` only when the bundle needs an explicit policy
override such as an allow-list or a hard deny.

If multiple SDK blocks can receive background jobs, do not add multiple
decorated `@on_job` methods. The final bundle entrypoint keeps one `@on_job`,
calls `await super().handle_job(**kwargs)` first, and only dispatches local
`work_kind` values when the superclass returns `handled=false`.

### Story-Aware Wizard Or Canvas With Agent Review

```text
main UI
  -> side chat iframe
  -> wizard or canvas iframe
  -> bundle APIs save product state and host domain files
  -> authored external events carry story_id, agent_id, and snapshot/artifact refs
  -> ReAct event-source policies produce timeline blocks
  -> rehosters materialize owner refs such as cnv:/mem:/task:/nmsp: into fi: refs when react.pull is called
```

Bundle code owns the story identity model, event-source ids, snapshot storage,
artifact namespace rehosters, and policies for how those events appear to the
agent. UI code owns the interaction surface and sends explicit product events.

### Telegram Bot Transport And Optional Mini App Controls

```text
Telegram Integration
  -> public telegram_webhook route with Telegram header-secret auth
  -> webhook validation, idempotency, and update normalization
  -> mapped user/conversation scope
  -> shared chat ingress submission or direct workflow fallback
  -> normal bundle entrypoint / ReAct agent
  -> progress streaming and final Bot API send of text/files
  -> optional Mini App initData auth, widget operation helpers, signed downloads
```

Bundle code owns role policy, which conversations can be selected, which
operations are public, and what workflow handles a message.

When the request is "add Telegram integration", read that as a transport
adapter unless the user specifically asks only for a Mini App. The expected
flow is:

```text
Telegram user message / attachment
  -> Telegram Bot API webhook call
  -> bundle public API: telegram_webhook
  -> SDK user_admin.handle_webhook(...)
  -> Telegram update -> KDCube ExternalEventPayload / RawAttachment
  -> shared chat ingress + bundle entrypoint
  -> SDK run_with_queued_telegram_delivery(...)
  -> Telegram Bot API text/file delivery
```

The reference implementation for this bot transport path is the versatile
bundle:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`.
Task-specific Mini App screens can layer on top of the same Telegram SDK
modules, but they are not required for the bot transport itself.

The versatile reference bundle also demonstrates a Telegram Mini App style
source-folder widget with memory canvas, chat channel selection, and Telegram
admin routes:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/widgets/telegram_miniapp`.

For shared Telegram UI, do not copy admin/channel panels into every bundle.
Use `sdk://integrations/telegram/ui/widget.telegram` as a widget
`shared_sources` entry, import `@kdcube/telegram-widget`, and inject the
bundle's operation caller. The panels are UI only; backend operations still own
KDCube role checks, Telegram `initData` verification, and Telegram registry
roles.

Before implementing, read the Telegram SDK bundle wiring checklist:
[Telegram SDK Integration](../../integrations/telegram/telegram-README.md).
The expected shape is SDK-first: configure `user_admin`; expose a thin
`telegram_webhook` handler; wrap queued turns with
`run_with_queued_telegram_delivery`; and keep registry admin operations on
KDCube-authenticated operations routes. If the product also includes a
Telegram Mini App, additionally configure `widget_auth`, `widget_ops`, and
`webapp`, and keep those public APIs behind Telegram `initData` validation.
Do not put generic KDCube `roles_config`/`user_types_config` gates on Telegram
Mini App public aliases; those requests are public at the platform route and
the bundle verifies the signed Telegram identity before applying Telegram
registry roles.

When local Telegram webhook or Mini App testing must be reachable from
Telegram, use
[Serving Local KDCube With Ngrok](../../../service/cicd/ngrok-README.md).
That flow exposes one public HTTPS origin through the local reverse proxy; it
does not expose proc as a separate public URL.

### Email-Enabled Assistant

```text
Email Integration
  -> account store and settings routes
  -> Gmail OAuth/API or iCloud IMAP/SMTP
  -> attachment materialization
  -> Email MCP for scoped message access
  -> Claude Code email-processing subagent when needed
```

Bundle code owns the product instruction, account selection policy, delivery
choice, and UI affordances for connecting accounts.

### User Memory Block

Use the memory entrypoint mixin when a bundle needs user-owned durable memories,
the Memory widget, optional ReAct announce hotsets, and snapshot-backed
reconciliation.

Descriptor config example:

```yaml
config:
  memory:
    enabled: true
    announce: {enabled: true, limit: 6, scope_filter: current_bundle}
    tools: {enabled: true, allow_write: false, default_scope_filter: current_bundle}
    widget: {enabled: true, allow_write: true, default_scope_filter: current_bundle}
    reconciliation: {enabled: true}
    snapshots: {enabled: true}
  ui:
    widgets:
      memories:
        enabled: true
```

Only bundles deriving from the memory mixin interpret this block. Keep
`tools.allow_write: false` until the bundle has an explicit policy for
agent-authored durable memory changes.

If the memory mixin or another parent class already declares
`@ui_widget(alias="memories")`, `ui.widgets.memories` only selects the built UI
for that existing surface. It does not create the widget by itself. Hide the
inherited memory widget with `enabled.widget.memories: false`; replace its UI by
configuring `ui.widgets.memories.src_folder/build_command`.

### Multi-Component Host Scene

Use this recipe when the product is one workspace that composes several reusable
SDK components — for example a chat widget, the memory widget, the canvas board,
and a usage card — into a single host page, instead of one widget per surface.

```text
host scene (ui/scene)
  -> mounts SDK component iframes by alias (chat, memory, usage) + the canvas React component
  -> relays the runtime CONFIG_REQUEST/RESPONSE handshake to each iframe
  -> brokers component-to-component postMessage (context attach/focus, set-view, canvas ingress)
  -> subscribes to the Data Bus (canvas.patch writes, accounting.usage events)
  -> routes canvas object "open" through the resolver registry to the owning widget surface
```

The bundle owns the scene only as a composition layer:

- declare each embedded component with `@ui_widget(alias=...)` (plus an `@api`
  mount point), and point `ui.widgets.<alias>.src_folder` at the shared SDK
  source (`sdk://solutions/chat/ui/widget`,
  `sdk://context/memory/ui/widget/memories`,
  `sdk://solutions/canvas/ui/component`, …);
- serve the host page itself through `ui.main_view.src_folder: ui/scene`;
- register the object resolvers for every namespace the scene can open
  (`mem:`, `fi:`, `cnv:`, …) so canvas pins resolve to the right surface.

The scene host does not own object semantics. It knows which iframe surface is
mounted and how to deliver a command to it; the namespace owner's resolver
decides what "open" or "preview" means.

The working reference is the versatile bundle
(`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`):
host at `ui/scene`, aliases and resolver registry in `entrypoint.py`, and the
bundle-local design note `docs/design/scene-sdk-components.md`.

Before building one, read the composition walk-through and the routing contract:
[Scene Composition](../../../solutions/scene/scene-composition-README.md) for the
mount config, CONFIG handshake, and Data Bus wiring;
[Scene Surface Registry](../../../solutions/scene/scene-surface-registry-README.md)
for how a resolver `open` event reaches the target widget.

## Test The Assembly Boundary

When a bundle uses an SDK block, tests should prove the binding, not duplicate
the SDK internals:

- routes call the configured SDK module with the right user scope;
- public paths validate their external auth, for example Telegram `initData`;
- `enabled.*` descriptor config is treated as overrides over code defaults, so
  tests should not require every enabled surface to be written as `true`;
- deployment props/secrets are read from the documented paths;
- user-owned credentials are stored as user-scoped runtime state/secrets;
- generated files are exposed only through the supported artifact/download
  contract;
- scheduled jobs restore runtime context and do not ask the model to invent
  task ids, execution ids, account ids, or storage paths;
- bundle design docs say which SDK blocks are used and what policy remains in
  the bundle.

Use SDK-level tests for the reusable mechanics and bundle-local tests for the
product binding.

## Adding New Reusable Blocks

When adding a reusable block such as cross-conversation memory, knowledge-base
retrieval, or a neural processing subagent:

1. Put reusable mechanics under `sdk.integrations.*` or `sdk.solutions.*`.
2. Keep product policy and route aliases in the bundle.
3. Add an SDK doc for the package.
4. Add an external-prerequisites doc if the integration needs provider/admin
   setup outside KDCube.
5. Add the block to this assembly map and to the Tier 1 navigation page.
6. Add SDK tests for the reusable package and bundle tests for the binding.
