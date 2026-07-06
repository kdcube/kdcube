---
id: docs/sdk/solutions/scene/config/README.md
title: "Scene Configuration Examples"
summary: "Frontend and backend config examples for composing a generic scene from apps, surfaces, named services, events, and runtime scopes."
status: draft
tags: ["sdk", "solutions", "scene", "configuration", "apps", "named-services", "event-bus", "data-bus"]
updated_at: 2026-07-07
see_also:
  - docs/sdk/solutions/scene/generic-scene-contract-README.md
  - docs/sdk/solutions/scene/scene-composition-README.md
  - docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - docs/sdk/namespace-services/README.md
  - docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
---
# Scene Configuration Examples

This folder holds concrete config examples for a website or app that composes a
KDCube scene.

Files:

| File | Purpose |
| --- | --- |
| [`website-scene.config.example.json`](website-scene.config.example.json) | Browser-side scene config: runtimes, components, target surfaces, drop targets, object-action gateway, namespace presentation, and command contracts. |
| [`backend-bundles.yaml.example`](backend-bundles.yaml.example) | Backend runtime config shape needed by the apps that participate in the scene: widgets, named-service providers/clients, ReAct event sources, canvas resolvers, Event Bus producers, Data Bus operations, and cron jobs. |

These examples intentionally separate the frontend scene contract from backend
provider registration.

The component registry itself has two delivery channels that share one model:

| Channel | Owner | Example |
| --- | --- | --- |
| Browser-owned config file | The host page ships its own scene config. | `website-scene.config.example.json` (the website page's `kdcube.config.json`). |
| Server-configured (`scene_surface_config`) | The app serves the component map from its runtime config; the browser scene fetches it at boot. | `surfaces.as_consumer.ui.scene.components` in the app's `bundles.yaml`, returned by the app's `scene_surface_config` operation. |

## Server-Configured Scene Components

An app-owned scene host (the workspace app's scene is the reference) reads its
component set from `surfaces.as_consumer.ui.scene`:

```yaml
surfaces:
  as_consumer:
    ui:
      scene:
        components:
          stats:
            enabled: true
            bundle_id: kdcube.stats@2026-05-20-12-05
            widget_alias: usage
            route: public/widgets/usage
            title: Stats
            accent: orange
            gated: false
            views: true
            size: { w: 720, h: 520 }
            order: 40
        external_panels:
          - id: task_panel
            bundle_id: task-tracker@1-0
            widget_alias: task_tracker_tasks
            # ... surfaces / event forwarding — see
            # "External Panels And Provider-Open Routing" below
```

`scene_surface_config` returns `components`, `external_panels`, and
`namespace_styles` in one payload. The scene ships built-in defaults —
`pinboard` (the app's served pin board widget), `chat`, `memories` and
`memory_item` (both iframed from `user-memories@2026-06-26`), and `usage` — and
merges config entries over them by alias: an entry overrides a default,
`enabled: false` removes it, and a new alias mounts any deployed app's served
widget as one more iframe surface.

Per-component keys:

| Key | Meaning |
| --- | --- |
| `bundle_id` + `widget_alias` (or `route`) | Owning app package and its served widget; `route` overrides the widget path when the app exposes a public route. |
| `title`, `accent`, `size {w,h}`, `full {w,h}`, `order` | Rail/window presentation. |
| `gated` | Hidden from the rail and closed unless the viewer is authenticated. |
| `views` | The widget supports `kdcube-set-view` compact/expanded. |
| `target_surfaces` | Surfaces the component registers in the scene runtime. |
| `drop { effect, patterns, target_surface }` | Context-drop acceptance. `target_surface` lets an `open` drop resolve toward a surface owned by ANOTHER component: the `memories` list accepts `mem:*` drops that open in `sdk.memory.viewer`, owned by the `memory_item` editor component. |
| `placement: docked \| floating` | `docked` components sit in a static stage slot and unpin into a floating window; `pinboard` and `chat` are docked by default. |
| `rail` | `false` hides the rail button for surface-command-only components such as `memory_item`. |
| `default_open` | Floating component summoned once its gate allows. |

The docked/floating window behavior behind `placement` is described in
[Scene Composition](../scene-composition-README.md).

## External Panels And Provider-Open Routing

`external_panels` mounts another app's served widget as a summonable panel and
declares which target surfaces that panel owns. This is the second half of the
provider-open contract: when a provider resolves `object.action(open)` and
returns `ui_event.target_surface`, the scene looks the surface up in its
runtime registry — the surface may be owned by a component
(`target_surfaces`, e.g. `sdk.memory.viewer` on the `memory_item` component)
or by an external panel (`surfaces`). Both resolve through the same registry;
the panel adds a per-surface descriptor that tells the host what to do with
the open:

```yaml
external_panels:
  - id: task_panel
    label: Tasks
    bundle_id: task-tracker@1-0
    widget_alias: task_tracker_tasks
    widget_message_type: kdcube-task-tracker-widget-command
    open_message_types:
      - kdcube-task-tracker-open-issue
      - kdcube-task-tracker-create-issue
    surfaces:
      task_tracker.issue_list:
        expanded: false
        command: { action: refresh }
      task_tracker.issue_editor:
        expanded: true
        command_from_open: provider_surface_open
```

Panel-level keys:

| Key | What the host does with it |
| --- | --- |
| `bundle_id` + `widget_alias` | The served widget iframed into the panel window. |
| `widget_message_type` | The widget's own host-command vocabulary. Every command delivered to a panel surface is posted with this `type` (plus `widget: <widget_alias>`), so the widget recognizes it; without this key the scene-level `kdcube.surface.command` type is kept. |
| `open_message_types` | Widget-emitted intents that summon the panel expanded (the widget asks its host to open the owning editor surface). |
| `service_event_type` / `service_forward_message_type` | One claimed project service event forwarded into the mounted widget under the given message type. |

Per-surface descriptor keys (`surfaces.<target_surface>`):

| Key | What the host does with it |
| --- | --- |
| `expanded` | Window state applied when an open resolves toward this surface: `true` summons the panel expanded (full size), `false` compact. |
| `command` | Static command posted to the widget when this surface is targeted (e.g. `{action: refresh}` for a list surface). |
| `command_from_open: provider_surface_open` | Forward the provider's open payload as the widget command: the scene builds the command from the open response (`action: open`, `object_ref`, `ui_event` fields, title) and posts it — under `widget_message_type` — so the widget switches to the right view with that object loaded. A surface declaring a static `command` and no `command_from_open` posts only the static command. |

With the example above, opening a `task:issue:` pin (or a task from chat)
resolves to `ui_event.target_surface = task_tracker.issue_editor`: the scene
summons the Tasks panel expanded and posts
`{type: kdcube-task-tracker-widget-command, action: open, object_ref: task:issue:..., ...}`
into the widget — the editor view opens with that issue. The same open routed
at `task_tracker.issue_list` just refreshes the compact list.

The reusable implementation lives in `@kdcube/components-react/scene`
(`externalPanelSurfaceRegistrations`); any scene host that registers panel
surfaces through it gets this contract. The full referent panel shape is in
the workspace app's config template
(`surfaces.as_consumer.ui.scene.external_panels`).

```text
website/app scene config
  declares where components mount, which app/runtime serves them,
  which target surfaces they expose, and which object-action gateway to call

backend app config
  declares which apps provide named services, widgets, events, cron jobs,
  ReAct event-source policies, and canvas/provider resolver policies
```

The scene host should not hardcode namespace semantics. It may contain host
composition policy such as "the task editor surface is mounted at this rail" or
"the chat surface accepts attach drops". It must not decide what a `mem:`,
`task:`, `conv:fi:`, or `conv:` object means beyond passing the full object ref to a
configured provider or surface.

## Genericity Status

The current implementation is clean enough for the website scene integration
path: scene mechanics are config-driven, object opens go through a configured
object-action gateway, and canvas pins preserve opaque provider refs. The
remaining non-generic pieces are explicit compatibility/presentation policy or
known follow-up cleanup, not hidden behavior branches in the scene host.

| Area | Current status | Remaining non-generic behavior |
| --- | --- | --- |
| Website scene (`website/index.html`, `website/scene-summon.js`, `website/scene-context-drag.js`) | Config-driven. Components, target surfaces, drop targets, namespace presentation, ready rules, runtime profiles, object-action gateway, and command contracts are read from `kdcube.config.json`. | Drop-target compatibility may use explicit host policy patterns such as `conv:*`, `mem:*`, or `task:*`. This is acceptable composition config for now; provider/surface-declared compatibility is the stronger future target. |
| `@kdcube/components-core/scene` | Generic surface-command, drag/drop, and event-claim runtime. It routes `kdcube.surface.command`, delegates open through an injected `objectAction` client, accepts drop compatibility from config, and dispatches events only to aliases that registered claims. | Drop compatibility may still use explicit selector patterns in deployment config; provider/surface-declared compatibility is the stronger future target. |
| `@kdcube/components-core/canvas` | Provider pins are stored as opaque `object_ref`; canvas-owned content uses canvas-owned refs and kinds. Card `kind` is a display label, not a behavior selector. | Some chat/file UI paths still know the durable `conv:fi:` direct-download shape. This should move behind object-action/provider capabilities. Canvas-owned text labels such as `provided.text` and `agent.text` remain local hosted-content labels, not provider-object routing keys. |

## What Belongs In Scene Config

Scene config owns:

- runtime aliases: origin, tenant, project, auth provider;
- component aliases: app id, route, runtime, mount policy, size, rail, target surfaces;
- host-visible surface command contracts;
- context drag/drop policies and effects;
- namespace presentation endpoint;
- object-action gateway endpoint;
- profile selection for local, staging, demo, and mixed-runtime websites.

Scene config does not own:

- per-component event subscription lists;
- provider-specific payload builders;
- object URI parsing rules;
- memory/task/file/conversation behavior;
- ReAct block production policies;
- provider schemas.

Components should claim the events they need after scene configuration is
delivered. Providers should resolve object actions and return target surfaces,
capabilities, and UI events. The scene relays and routes those results.

## Config-Driven Test Checklist

When checking whether a scene remains generic:

1. Search host code for concrete namespace prefixes such as `mem:`, `task:`,
   `conv:`, and `conv:fi:`. Host config may contain explicit compatibility policy
   for a composed website, but host code should not branch on these prefixes.
2. Search host code for app ids such as `workspace@...`, `task-tracker@...`,
   `news@...`, or `kdcube.stats@...`. They should appear in config, not core
   scene logic.
3. Drop an object onto every target surface. The scene should either dispatch
   a generic `kdcube.surface.command` or call the configured object-action
   gateway with the full `object_ref`.
4. Open a provider object from canvas. The canvas should call a resolver/action
   provider and use returned presentation/capabilities, not infer behavior from
   card kind.
5. Run one chat turn and verify event claims: usage card refresh, stats
   snapshot, and task/memory updates should be routed through component claims
   or provider event policies, not host fallback subscriptions.
